# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-subgraph dispatch tests for ``target_taxids``.

``target_taxids`` routes its taxonomy-extraction chat call through the
compiled chat subgraph (``_cached_chat_app().ainvoke``) with the shared
``chat_adapters`` IO mappers; these tests pin that the parsed
``target_spa_list`` response drives the returned sentinel.
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


async def test_target_taxids_routes_through_chat_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat extraction delegates to the compiled chat subgraph."""
    _legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        _EVO_MODULE,
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
