# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-subgraph dispatch tests for ``_extract_goals``.

``_extract_goals`` (the chat-site chokepoint inside
``InSilicoResearchAgents``) routes through ``_cached_chat_app().ainvoke``
with the shared ``chat_adapters`` IO mappers.
"""

# pylint: disable=protected-access
# Test file exercises ``_extract_goals`` (the chat-site chokepoint
# inside InSilicoResearchAgents) directly to assert chat routing.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research import agent as research_agent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
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

    result = await agent._extract_goals("Paper text body", [])

    assert result == [{"goal": "Investigate X", "context": "context-blob"}]
    subgraph_app_mock.ainvoke.assert_awaited_once()
