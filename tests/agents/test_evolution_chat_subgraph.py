# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for target_taxids' chat-subgraph dispatch.

Pins ``USE_CHAT_SUBGRAPH``: flag-off keeps the legacy ``phyto_chat``
call; flag-on routes through ``_cached_chat_app().ainvoke`` with the
shared ``chat_adapters`` IO mappers.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.evolution import agent as evolution_agent
from mcp_server_phytomni.agents.evolution.agent import target_taxids

from ._subgraph_branch_fakes import install_chat_branch_mocks

pytestmark = pytest.mark.agent

_EVO_MODULE = "mcp_server_phytomni.agents.evolution.agent"


def _content(text: str) -> dict[str, object]:
    """Wrap a single chat-completion content payload for tests."""
    return {"choices": [{"message": {"content": text}}]}


async def test_target_taxids_uses_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits the legacy ``phyto_chat`` directly."""
    monkeypatch.setattr(
        evolution_agent.DEEP_GENOME_CONFIG, "USE_CHAT_SUBGRAPH", False
    )
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _EVO_MODULE,
        legacy_response=_content('{"target_spa_list": ["All"]}'),
        subgraph_response={"choices": [{"message": {"content": ""}}]},
    )
    monkeypatch.setattr(
        evolution_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await target_taxids("Find Arabidopsis homologs", {})

    assert result == "All"
    legacy_mock.assert_awaited_once()
    subgraph_app_mock.ainvoke.assert_not_awaited()


async def test_target_taxids_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to the compiled chat subgraph."""
    monkeypatch.setattr(
        evolution_agent.DEEP_GENOME_CONFIG, "USE_CHAT_SUBGRAPH", True
    )
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _EVO_MODULE,
        legacy_response=_content('{"target_spa_list": ["wrong"]}'),
        subgraph_response={
            "response": _content('{"target_spa_list": ["All"]}')
        },
    )
    monkeypatch.setattr(
        evolution_agent, "get_prompt", lambda *_a, **_kw: "prompt-stub"
    )

    result = await target_taxids("Find Arabidopsis homologs", {})

    assert result == "All"
    subgraph_app_mock.ainvoke.assert_awaited_once()
    legacy_mock.assert_not_awaited()
