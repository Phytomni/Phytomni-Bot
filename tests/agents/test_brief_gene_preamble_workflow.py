# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end fan-in safety tests for the brief_gene preamble graph.

Drives the compiled graph with mocked BI / section LLM calls under an
asyncio timeout to prove the section fan-in resolves on BOTH the
gene-found path (parallel annotation/homology + retrieve) and the
gene-not-found path (homology short-circuits, sections run literature
only) without deadlocking, and that the render writes a full preamble.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_FOUND_ROW = {
    "message": "ok",
    "data": [
        {
            "gene_id": "Os01g0177400",
            "species_code": "osa",
            "id_type": "rap",
            "symbol": "OsCAB1",
            "chromosome": "1",
            "start": "1",
            "end": "9",
            "strand": "+",
            "description": "stub",
        }
    ],
}
_EMPTY_ROW = {"message": "ok", "data": []}
_CHAT_STUB = {"choices": [{"message": {"content": "stub section/intro"}}]}


def _install_mocks(
    monkeypatch: pytest.MonkeyPatch, *, bi_response: dict[str, Any]
) -> None:
    """Patch every external call the preamble graph makes."""
    bi_mock = AsyncMock(return_value=bi_response)
    chat_mock = AsyncMock(return_value=_CHAT_STUB)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api", bi_mock
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.homology.relay_bi_query",
        AsyncMock(return_value=_EMPTY_ROW),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections."
        "phyto_chat",
        chat_mock,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
        chat_mock,
    )


async def _run_preamble(user_query: str) -> str:
    """Invoke the compiled graph (no follow-up) and return the content."""
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    final_state = await asyncio.wait_for(
        agent.app.ainvoke(
            {"user_query": user_query, "is_follow_up": False},
            config={"configurable": {"thread_id": "preamble-test"}},
        ),
        timeout=20,
    )
    return final_state["final_response"]["choices"][0]["message"]["content"]


async def test_preamble_gene_found_fan_in_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_found path: parallel fetch + 4-section fan-in reaches render.

    The four section nodes gate on BOTH retrieve_reduce and
    fetch_homology; this asserts that join resolves (no deadlock) and
    the render writes the full preamble skeleton.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)

    content = await _run_preamble("Os01g0177400")

    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content


async def test_preamble_gene_not_found_fan_in_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_not_found path: homology short-circuits, fan-in still resolves.

    With no resolved gene the homology node returns empty immediately,
    yet it still runs (static edge off query_judge) so the section
    fan-in never waits on a branch that did not fire — the graph must
    still reach the render rather than deadlock.
    """
    _install_mocks(monkeypatch, bi_response=_EMPTY_ROW)

    content = await _run_preamble("an unresolvable free-form query")

    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content
