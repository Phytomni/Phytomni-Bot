# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for _extract_goals' chat-subgraph dispatch.

Pins ``USE_CHAT_SUBGRAPH``: flag-off keeps the legacy ``phyto_chat``
call; flag-on routes through ``_cached_chat_app().ainvoke`` with the
shared ``chat_adapters`` IO mappers.
"""

# pylint: disable=protected-access
# Test file exercises ``_extract_goals`` (the chat-site chokepoint
# inside InSilicoResearchAgents) directly to assert flag routing.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research import agent as research_agent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_branch_mocks

pytestmark = pytest.mark.agent

_RESEARCH_MODULE = "mcp_server_phytomni.agents.research.agent"


def _build_agent(use_chat_subgraph: bool) -> InSilicoResearchAgents:
    """Construct an InSilicoResearchAgents with USE_CHAT_SUBGRAPH set."""
    config = InSilicoResearchConfig().model_copy(
        update={"USE_CHAT_SUBGRAPH": use_chat_subgraph}
    )
    return InSilicoResearchAgents(
        in_silico_config=config,
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, SimpleNamespace()),
    )


def _content(goals: list[dict[str, str]]) -> dict[str, object]:
    """Wrap a goals-list payload in chat-completion shape."""
    return {"choices": [{"message": {"content": json.dumps(goals)}}]}


async def test_extract_goals_uses_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits the legacy ``phyto_chat`` directly."""
    agent = _build_agent(use_chat_subgraph=False)
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _RESEARCH_MODULE,
        legacy_response=_content(
            [{"goal": "Investigate X", "context": "context-blob"}]
        ),
        subgraph_response={"choices": [{"message": {"content": "[]"}}]},
    )
    monkeypatch.setattr(
        research_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await agent._extract_goals("Paper text body", [])

    assert result == [{"goal": "Investigate X", "context": "context-blob"}]
    legacy_mock.assert_awaited_once()
    subgraph_app_mock.ainvoke.assert_not_awaited()


async def test_extract_goals_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to the compiled chat subgraph."""
    agent = _build_agent(use_chat_subgraph=True)
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _RESEARCH_MODULE,
        legacy_response=_content([{"goal": "wrong", "context": "wrong"}]),
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
    legacy_mock.assert_not_awaited()
