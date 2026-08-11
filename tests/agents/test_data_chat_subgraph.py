# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``DataAgent`` chat invocations through the chat subgraph.

The rewrite chat site routes through a prep + post pair surrounding a
single shared chat node registered via ``mount_chat_node`` from the
``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

from typing import cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.data.state import (
    DataAgentState,
    DataInput,
)
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.subgraph_fakes import (
    DATA_CHAT_MOUNT_TOPOLOGY,
    assert_chat_mount_topology,
    install_knowledge_app,
)

from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_DATA_MODULE = "mcp_server_phytomni.agents.data.agent"

_CHAT_COMPLETION_RESPONSE = {
    "choices": [{"message": {"content": "rewritten sql-friendly query"}}],
    "usage": {"total_tokens": 17},
}


def _build_agent(monkeypatch: pytest.MonkeyPatch) -> DataAgent:
    """Construct a ``DataAgent`` with the chat subgraph mounted.

    The knowledge subgraph is always mounted at the retrieve site, so
    the shared recording fake keeps construction offline, then
    isolates the chat-subgraph mount under test.
    """
    install_knowledge_app(monkeypatch, f"{_DATA_MODULE}.build_knowledge_app")
    return DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _minimal_rewrite_state() -> DataAgentState:
    """Return the minimal state the rewrite prep node reads.

    The rewrite prep node only reads ``retrieve_prompt`` (the prompt
    already stitched by ``retrieve_post_node``); no other keys are
    read at this node, so the dict stays minimal.
    """
    return cast(
        DataAgentState,
        {"retrieve_prompt": "stitched scenarios + user question"},
    )


def test_compiled_graph_preserves_chat_mount_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin Data's public schema, routes, xray, and checkpointer contract."""
    agent = _build_agent(monkeypatch)
    assert_chat_mount_topology(
        agent.app,
        agent.checkpointer,
        DATA_CHAT_MOUNT_TOPOLOGY,
    )


async def test_rewrite_prep_node_builds_chat_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prep node stages the ``chat_payload`` for the shared chat node.

    The prep node owns the prompt-passing half of the legacy
    ``rewrite_node`` and emits a single state-delta key: the
    ``ChatInput`` payload destined for the shared chat node. No
    chat call happens here, and DataAgent's single chat call site
    needs no ``pending_post`` sentinel because the after-chat edge
    is unconditional.
    """
    agent = _build_agent(monkeypatch)
    result = await agent.rewrite_prep_node(_minimal_rewrite_state())

    chat_payload = result["chat_payload"]
    assert chat_payload["user_query"] == ("stitched scenarios + user question")
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-data"
    )
    assert "obs_file_list" not in chat_payload


async def test_rewrite_post_node_extracts_rewrite_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node extracts ``rewrite_query`` from the chat response.

    The post node mirrors the response-validation + content-extraction
    half of the legacy ``rewrite_node`` but reads the chat response
    from ``state['chat_response']`` (written by the shared chat node)
    instead of awaiting a fresh ``phyto_chat`` call.
    """
    agent = _build_agent(monkeypatch)
    state = cast(
        DataAgentState,
        {
            "chat_response": {
                "choices": [{"message": {"content": "rewritten Q"}}]
            }
        },
    )
    result = await agent.rewrite_post_node(state)

    assert result == {"rewrite_query": "rewritten Q"}


async def test_rewrite_post_node_raises_on_empty_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node raises ``McpError`` when the chat response is missing.

    Pins the upstream-failure contract: when the shared chat node
    returns an empty or choices-less dict, the post node raises the
    same ``McpError`` the legacy ``rewrite_node`` raised so the
    failure mode stays observable across both graph shapes.
    """
    agent = _build_agent(monkeypatch)
    empty_state = cast(DataAgentState, {})
    with pytest.raises(McpError):
        await agent.rewrite_post_node(empty_state)

    no_choices_state = cast(DataAgentState, {"chat_response": {"choices": []}})
    with pytest.raises(McpError):
        await agent.rewrite_post_node(no_choices_state)


async def test_compiled_graph_routes_through_shared_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph awaits the shared chat subgraph.

    End-to-end exercise of the prep + chat + post split: the
    compiled graph routes the rewrite chat call through the single
    registered ``chat`` node so the patched shared ``CHAT_APP.ainvoke``
    mock is awaited once with the prep-built ``ChatInput``, and the
    final ``final_response`` propagates through ``search_node`` to
    the graph output.
    """
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_DATA_MODULE,
        legacy_response=None,
        subgraph_response=_CHAT_COMPLETION_RESPONSE,
    )

    async def fake_retrieve_post_node(
        _self: DataAgent, _state: DataAgentState
    ) -> dict:
        """Stub ``retrieve_post_node`` so the test never hits the backend.

        The retrieve site always routes ``retrieve_prep_node`` ->
        ``knowledge`` -> ``retrieve_post_node``; the offline knowledge
        stub returns no docs, so the post node is stubbed to emit the
        same fixed ``retrieve_prompt`` the unit tests use, giving the
        rewrite prep node a deterministic input.
        """
        return {
            "retrieve_prompt": "stitched scenarios + user question",
        }

    async def fake_search_node(
        _self: DataAgent, state: DataAgentState
    ) -> dict:
        """Stub ``search_node`` so the test never hits the NL2SQL service.

        Captures the post-node-produced ``rewrite_query`` and echoes
        it back inside ``final_response`` so the test can assert the
        prep+chat+post pipeline assembled the expected query.
        """
        return {
            "final_response": {"echoed_query": state["rewrite_query"]},
        }

    # Patch the class methods BEFORE constructing the agent so the
    # original ``_build_graph`` call captures the stubs instead of
    # the real backend-hitting bound methods.
    monkeypatch.setattr(
        DataAgent, "retrieve_post_node", fake_retrieve_post_node
    )
    monkeypatch.setattr(DataAgent, "search_node", fake_search_node)
    agent = _build_agent(monkeypatch)
    initial_input = cast(
        DataInput,
        {
            "user_query": "How many genes were sequenced last year?",
            "is_rewrite": True,
        },
    )
    final_state = await agent.app.ainvoke(
        initial_input,
        config={"configurable": {"thread_id": "test-thread"}},
    )

    legacy_mock.assert_not_awaited()
    fake_chat_app.ainvoke.assert_awaited_once()
    chat_input = fake_chat_app.ainvoke.await_args.args[0]
    assert chat_input["user_query"] == ("stitched scenarios + user question")
    assert isinstance(chat_input["chat_kwargs"], dict)
    assert len(chat_input["chat_kwargs"]) == 18
    assert chat_input["chat_kwargs"]["relay_timeout_profile"] == "phyto-data"
    assert "obs_file_list" not in chat_input
    assert final_state["final_response"] == {
        "echoed_query": "rewritten sql-friendly query"
    }


def test_compiled_graph_xray_expands_chat_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph exposes the shared chat subgraph to ``xray``.

    Structural check: ``StateGraph.get_graph(xray=True)`` walks the
    compiled graph and inlines any node whose body closes over a
    ``CompiledStateGraph``. Mounting chat via
    ``make_chat_node_wrapper(...)`` keeps the compiled subgraph at
    the wrapper's module-level globals, so ``find_subgraph_pregel``
    discovers it and the xray render carries node keys prefixed with
    ``chat:`` (the parent node name plus the subgraph node names).
    A flat ``chat`` key with no child prefix would mean the wrapper
    hid the subgraph behind another closure and the render reverted
    to an opaque box.
    """
    agent = _build_agent(monkeypatch)
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
