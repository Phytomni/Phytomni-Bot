# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-subgraph dispatch tests for ``_extract_goals``.

``_extract_goals`` (the chat-site chokepoint inside
``InSilicoResearchAgents``) routes through ``_cached_chat_app().ainvoke``
with the shared ``chat_adapters`` IO mappers.
"""

# The direct goal-parser probe below targets the smallest research chat seam;
# its protected-access directive is scoped to that test symbol.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research import agent as research_agent
from mcp_server_phytomni.agents.research import goal_extraction
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
)
from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_RESEARCH_MODULE = "mcp_server_phytomni.agents.research.agent"


def _build_agent() -> InSilicoResearchAgents:
    """Construct an InSilicoResearchAgents for chat-subgraph tests."""
    return InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, SimpleNamespace()),
    )


def _content(goals: list[dict[str, str]]) -> dict[str, object]:
    """Wrap a goals-list payload in chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(goals)}}]}


def _install_chat_app_mock(
    monkeypatch: pytest.MonkeyPatch,
    subgraph_response: dict[str, object],
) -> SimpleNamespace:
    """Patch ``_cached_chat_app`` to return a stubbed compiled subgraph."""
    subgraph_app_mock = SimpleNamespace(
        ainvoke=AsyncMock(return_value=subgraph_response)
    )
    monkeypatch.setattr(
        f"{_RESEARCH_MODULE}._cached_chat_app", lambda: subgraph_app_mock
    )
    return subgraph_app_mock


async def test_extract_goals_uses_chat_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_extract_goals`` delegates to the compiled chat subgraph."""
    agent = _build_agent()
    subgraph_app_mock = _install_chat_app_mock(
        monkeypatch,
        subgraph_response={
            "response": _content(
                [{"goal": "Investigate X", "context": "context-blob"}]
            ),
        },
    )
    monkeypatch.setattr(
        research_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await getattr(agent, "_extract_goals")("Paper text body", [])

    assert result == [{"goal": "Investigate X", "context": "context-blob"}]
    subgraph_app_mock.ainvoke.assert_awaited_once()


async def test_extract_goals_from_evidence_does_not_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evidence seam reuses extracted text and never downloads files."""
    agent = _build_agent()
    subgraph_app_mock = _install_chat_app_mock(
        monkeypatch,
        subgraph_response={
            "response": _content(
                [{"goal": "Investigate X", "context": "context-blob"}]
            ),
        },
    )
    monkeypatch.setattr(
        research_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )
    download = AsyncMock(side_effect=AssertionError("downloaded twice"))
    monkeypatch.setattr(goal_extraction, "download_upload_context", download)
    evidence = ExtractedResearchEvidence(
        units=(
            ResearchEvidenceUnit(
                evidence_id="query_span_001",
                source_kind="query",
                source_ordinal=0,
                source_span=None,
                content_digest="digest",
                text="Paper text body",
                dataset_ids=(),
            ),
        ),
        document_digests=(),
        coverage_digest="coverage",
    )

    result = await agent.extract_goals_from_evidence(evidence, "en-US")

    assert [item.goal for item in result] == ["Investigate X"]
    subgraph_app_mock.ainvoke.assert_awaited_once()
    download.assert_not_awaited()
