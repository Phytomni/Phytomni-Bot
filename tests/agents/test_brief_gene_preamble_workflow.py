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
import faulthandler
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._network_escape import install_network_escape_guard
from ._subgraph_branch_fakes import install_chat_subgraph_mocks

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
_KNOWLEDGE_OUTPUT = {
    "retrieved_docs": [
        {
            "chunk_id": "1",
            "score": 0.9,
            "title": "stub literature",
            "content": "stub literature chunk",
        },
    ],
    "retrieval_outcome": "complete",
    "final_response": {},
}


async def _stub_knowledge_ainvoke(
    _knowledge_input: Any, *_args: Any, **_kwargs: Any
) -> dict[str, Any]:
    """Return a canned ``KnowledgeOutput``-shaped doc list."""
    return _KNOWLEDGE_OUTPUT


def _install_mocks(
    monkeypatch: pytest.MonkeyPatch, *, bi_response: dict[str, Any]
) -> None:
    """Patch every external call the preamble graph makes.

    Covers the BI annotation / homology lookups, the section + intro
    chat completions, AND the mounted knowledge subgraph (the retrieve
    worker's ``knowledge_app.ainvoke``) so no real HTTP / relay / cache
    call can escape into the fan-in under test.
    """
    bi_mock = AsyncMock(return_value=bi_response)
    chat_mock = AsyncMock(return_value=_CHAT_STUB)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.build_knowledge_app",
        lambda *_args, **_kwargs: SimpleNamespace(
            ainvoke=_stub_knowledge_ainvoke
        ),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api", bi_mock
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.homology.run_bi_api",
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


@pytest.fixture(autouse=True)
def _fail_fast_on_network_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Convert any un-mocked outbound call into an instant named failure.

    The shared ``install_network_escape_guard`` patches the three async
    paths the repo-root ``block_external_http`` fixture leaves open
    (``httpx.*.send``, the loop's ``create_connection``, and
    ``getaddrinfo``); see ``tests/agents/_network_escape.py`` for the
    full rationale.
    """
    install_network_escape_guard(monkeypatch, label="preamble")


@pytest.fixture(autouse=True)
def _dump_stack_if_hung() -> Iterator[None]:
    """Auto-capture the blocked frame if the compiled graph wedges.

    This graph passes on this machine across every dependency resolve
    tried (langgraph 1.2.0 and 1.2.6, anyio 4.13/4.14, default loop and
    uvloop) and 40 cold-cache reruns, yet independent audits reproduce a
    hang their environment alone exhibits. ``faulthandler`` arms a
    watchdog *thread* that dumps every thread's stack after 12s — chosen
    to fire WHILE the ``await`` is still blocked, INSIDE this test's own
    20s ``wait_for`` (the prior 25s fired only after ``wait_for`` had
    already raised ``TimeoutError`` and teardown cancelled it, so it never
    captured anything). The watchdog runs off the event loop, so it fires
    even when a synchronous call wedges the loop (which ``wait_for`` cannot
    then cancel). A fast pass cancels it well before 12s so it never
    bleeds into a neighbouring test.
    """
    faulthandler.dump_traceback_later(12, exit=False)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


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

    The four section nodes gate on retrieve_reduce while fetch_homology
    runs in parallel off query_judge; this asserts the fan-in resolves
    (no deadlock) and the render writes the full preamble skeleton.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)

    content = await _run_preamble("Os01g0177400")

    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content


async def test_preamble_knowledge_exception_keeps_report_copy_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising knowledge subgraph degrades the run without warning copy.

    Drives the FULL compiled graph with a knowledge mount whose
    ``ainvoke`` raises, so every per-symbol ``retrieve_worker_node``
    hits its broad ``except``, records the failed index, and appends an
    internal ``literature_degraded`` record. The compiled graph must still
    reach ``render_node`` without exposing a source warning in the report.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    # Re-point the knowledge mount built in ``BriefGeneAgent.__init__``
    # at a stand-in whose ``ainvoke`` raises, so every per-symbol
    # retrieve worker hits its broad except (overrides the canned stub
    # installed above; the later setattr wins).
    raising_app = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=RuntimeError("knowledge unavailable"))
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.build_knowledge_app",
        lambda *_args, **_kwargs: raising_app,
    )

    content = await _run_preamble("Os01g0177400")

    assert content.startswith("# Brief Gene Analysis of")
    assert "⚠️" not in content
    # Degraded is status-independent: the full preamble skeleton still
    # renders rather than collapsing to a failure note.
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


async def test_preamble_follow_up_tail_fires_render_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_follow_up=True must not collide on the final_response channel.

    The four sections gate on retrieve_reduce (a deep superstep) while
    fetch_homology settles in a shallow one. A homology->section edge
    would fire the fan-in once per superstep, running every section and
    render twice; with the follow-up tail active a wave-2 render then
    collides with the wave-1 follow_up_post on the no-reducer
    ``final_response`` channel, raising ``InvalidUpdateError``. This
    drives the FULL graph with the follow-up tail — the arun default
    that the is_follow_up=False fan-in tests never exercise — so the
    single-fire fan-in is pinned end to end.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    install_chat_subgraph_mocks(
        monkeypatch,
        "mcp_server_phytomni.agents.brief_gene.core",
        legacy_response=_CHAT_STUB,
        subgraph_response=_CHAT_STUB,
    )
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    final_state = await asyncio.wait_for(
        agent.app.ainvoke(
            {"user_query": "Os01g0177400", "is_follow_up": True},
            config={"configurable": {"thread_id": "preamble-followup"}},
        ),
        timeout=20,
    )

    content = final_state["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
