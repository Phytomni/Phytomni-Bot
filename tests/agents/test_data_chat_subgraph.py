# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for ``DataAgent`` chat invocations.

Pins the ``USE_CHAT_SUBGRAPH`` flag branch on the data
``rewrite_node``: flag-off keeps the legacy direct
``phyto_chat(...)`` call, flag-on routes through
``_cached_chat_app().ainvoke(ChatInput)`` via the
``data_to_chat_adapters`` projection helpers. Both branches emit
the same ``rewrite_query`` downstream value.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.data.state import DataAgentState
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_branch_mocks

pytestmark = pytest.mark.agent

_DATA_MODULE = "mcp_server_phytomni.agents.data.agent"

_CHAT_COMPLETION_RESPONSE = {
    "choices": [{"message": {"content": "rewritten sql-friendly query"}}],
    "usage": {"total_tokens": 17},
}


def _build_agent(use_subgraph: bool) -> DataAgent:
    """Construct a ``DataAgent`` with ``USE_CHAT_SUBGRAPH`` set.

    Uses ``model_copy`` to flip the flag on the inherited
    ``ServerConfig`` field without tripping pylint ``C0103`` on a
    direct UPPERCASE attribute assignment, mirroring the
    ``_build_agent`` shape used by the knowledge-subgraph tests.
    """
    config = DataConfig().model_copy(
        update={"USE_CHAT_SUBGRAPH": use_subgraph}
    )
    return DataAgent(
        data_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _minimal_rewrite_state() -> DataAgentState:
    """Return the minimal state ``rewrite_node`` reads.

    ``rewrite_node`` only reads ``retrieve_prompt`` (the prompt
    already stitched by ``retrieve_node``); no other keys are read
    at this node, so the dict stays minimal.
    """
    return cast(
        DataAgentState,
        {"retrieve_prompt": "stitched scenarios + user question"},
    )


@pytest.mark.parametrize("use_subgraph", [False, True])
async def test_rewrite_node_respects_chat_subgraph_flag(
    use_subgraph: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off awaits ``phyto_chat``; flag-on awaits the chat subgraph.

    Both branches are exercised against the same minimal state with
    the same canned chat-completion response so the downstream
    ``rewrite_query`` value extracted from ``choices[0].message
    .content`` is identical regardless of which branch ran. The
    chat-subgraph branch returns its response under the ``response``
    key (the ``ChatOutput`` contract) which ``extract_chat_response``
    unwraps; the legacy branch returns the raw chat-completion dict
    directly.
    """
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_DATA_MODULE,
        legacy_response=_CHAT_COMPLETION_RESPONSE,
        subgraph_response={"response": _CHAT_COMPLETION_RESPONSE},
    )

    agent = _build_agent(use_subgraph=use_subgraph)
    result = await agent.rewrite_node(_minimal_rewrite_state())

    if use_subgraph:
        subgraph_app_mock.ainvoke.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        legacy_mock.assert_awaited_once()
        subgraph_app_mock.ainvoke.assert_not_awaited()

    assert result == {"rewrite_query": "rewritten sql-friendly query"}


async def test_rewrite_node_subgraph_branch_projects_chat_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on branch forwards a ``ChatInput`` carrying the retrieve prompt.

    Pins the projection contract: the adapter must hand the subgraph
    a ``ChatInput`` whose ``user_query`` is the ``retrieve_prompt``
    that ``retrieve_node`` already stitched, and whose
    ``chat_kwargs`` is the 17-key provider bag. The data node does
    not pass uploads, so ``obs_file_list`` must stay absent and the
    chat subgraph's ``prepare_context`` reads "no uploads" instead
    of iterating an empty list down to OBS.
    """
    _, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_DATA_MODULE,
        subgraph_response={"response": _CHAT_COMPLETION_RESPONSE},
    )

    agent = _build_agent(use_subgraph=True)
    await agent.rewrite_node(_minimal_rewrite_state())

    call_args = subgraph_app_mock.ainvoke.await_args
    assert call_args is not None
    chat_input = call_args.args[0]
    assert chat_input["user_query"] == "stitched scenarios + user question"
    assert isinstance(chat_input["chat_kwargs"], dict)
    assert len(chat_input["chat_kwargs"]) == 17
    assert "obs_file_list" not in chat_input
