# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``KnowledgeAgent`` chat-subgraph invocations.

The ``generate`` and ``follow_up`` chat calls route through prep +
post pairs surrounding a single shared chat node registered via
``mount_chat_node`` from the ``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.state import (
    KnowledgeInput,
    KnowledgeState,
)
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.subgraph_fakes import (
    KNOWLEDGE_CHAT_MOUNT_TOPOLOGY,
    assert_chat_mount_topology,
)

from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_KNOWLEDGE_MODULE = "mcp_server_phytomni.agents.knowledge.agent"

_CHAT_COMPLETION_RESPONSE = {
    "choices": [{"message": {"content": "synthesised answer"}}],
    "usage": {"total_tokens": 42},
}


def _build_agent() -> KnowledgeAgent:
    """Construct a ``KnowledgeAgent`` for the chat-subgraph path.

    Mirrors the ``build_branch_agent`` shape used by the
    analyst-subgraph tests; the chat subgraph is always mounted.
    """
    return KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _minimal_generate_state() -> KnowledgeState:
    """Return the minimal state ``generate_node`` reads.

    ``generate_node`` reads ``user_query`` / ``retrieve_context`` /
    ``upload_context`` (defaults to ``""`` when absent) and the
    ``retrieved_docs`` list it embeds into ``doc_list_payload``. No
    other keys are read at this node, so the dict stays minimal.
    """
    return cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "retrieve_context": "doc1 ... doc2 ...",
            "upload_context": "",
            "retrieved_docs": [{"title": "Plant Biology.pdf"}],
        },
    )


_FOLLOW_UP_CHAT_RESPONSE = {
    "choices": [
        {"message": {"content": '["next question one", "next question two"]'}}
    ],
}


def _minimal_follow_up_state() -> KnowledgeState:
    """Return the minimal state ``follow_up_node`` reads.

    ``follow_up_node`` reads ``user_query`` and ``main_response``; the
    latter must expose ``choices[0].message.content`` so
    ``message_content`` can extract the prior assistant turn and so
    the downstream ``update({"follow_up_questions": ...})`` patch
    finds a writable message dict.
    """
    return cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "main_response": {
                "choices": [{"message": {"content": "primary answer body"}}]
            },
        },
    )


def test_compiled_graph_preserves_chat_mount_topology() -> None:
    """Pin Knowledge's schema, routes, xray, and checkpointer contract."""
    agent = _build_agent()
    assert_chat_mount_topology(
        agent.app,
        agent.checkpointer,
        KNOWLEDGE_CHAT_MOUNT_TOPOLOGY,
    )

    assert (
        agent.route_start(
            cast(KnowledgeState, {"obs_file_list": ["obs://file"]})
        )
        == "process_files_node"
    )
    assert agent.route_start(cast(KnowledgeState, {"obs_file_list": []})) == (
        "retrieve_node"
    )
    assert (
        agent.route_after_retrieve(cast(KnowledgeState, {"is_generate": True}))
        == "generate_node"
    )
    assert (
        agent.route_after_retrieve(
            cast(KnowledgeState, {"is_generate": False})
        )
        == "__end__"
    )
    assert (
        agent.route_after_generate(
            cast(KnowledgeState, {"is_follow_up": True})
        )
        == "follow_up_node"
    )
    assert (
        agent.route_after_generate(
            cast(KnowledgeState, {"is_follow_up": False})
        )
        == "__end__"
    )


async def test_generate_prep_node_builds_chat_payload_and_pending_post() -> (
    None
):
    """Prep node stages ``chat_payload`` plus the post-node sentinel.

    The prep node owns the prompt-stitching half of the legacy
    ``generate_node`` and emits exactly two state-delta keys: the
    ``ChatInput`` payload destined for the shared chat node, and the
    ``pending_post`` sentinel the after-chat router reads to branch
    back to ``generate_post_node``. No chat call happens here.
    """
    agent = _build_agent()
    result = await agent.generate_prep_node(_minimal_generate_state())

    assert result["pending_post"] == "generate_post_node"
    chat_payload = result["chat_payload"]
    assert "doc1" in chat_payload["user_query"]
    assert "What is photosynthesis?" in chat_payload["user_query"]
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-knowledge"
    )
    assert "obs_file_list" not in chat_payload


async def test_generate_post_node_merges_doc_list_into_chat_response() -> None:
    """Post node merges retrieved docs into the chat-subgraph response.

    The post node mirrors the doc-list-merge half of the legacy
    ``generate_node`` but reads the chat response from
    ``state['chat_response']`` (written by the shared chat node)
    instead of awaiting a fresh ``phyto_chat`` call. Both
    ``main_response`` and ``final_response`` are populated so a
    follow-up node can read ``main_response`` and a terminal seam
    can read ``final_response``.
    """
    agent = _build_agent()
    state = cast(
        KnowledgeState,
        {
            "retrieved_docs": [{"title": "Plant Biology.pdf"}],
            "chat_response": dict(_CHAT_COMPLETION_RESPONSE),
        },
    )
    result = await agent.generate_post_node(state)

    assert result["main_response"] is result["final_response"]
    message = result["main_response"]["choices"][0]["message"]
    assert message["content"] == "synthesised answer"
    assert message["doc_list"] == [{"title": "Plant Biology.pdf"}]
    assert message["total"] == 10000


async def test_follow_up_prep_node_builds_chat_payload_and_pending_post() -> (
    None
):
    """Prep node stages the follow-up payload + post-node sentinel.

    The prep node owns the follow-up prompt assembly and emits the
    ``ChatInput`` plus a ``pending_post`` of ``follow_up_post_node``
    so the after-chat router returns to the follow-up parser instead
    of the generate post node.
    """
    agent = _build_agent()
    result = await agent.follow_up_prep_node(_minimal_follow_up_state())

    assert result["pending_post"] == "follow_up_post_node"
    chat_payload = result["chat_payload"]
    assert "What is photosynthesis?" in chat_payload["user_query"]
    assert "primary answer body" in chat_payload["user_query"]
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-knowledge"
    )
    assert "obs_file_list" not in chat_payload


async def test_follow_up_post_node_parses_and_merges_follow_up_questions() -> (
    None
):
    """Post node parses and merges the follow-up questions list.

    The post node mirrors the parse + mutate half of the legacy
    ``follow_up_node`` and reads ``state['chat_response']`` instead
    of awaiting a fresh chat call. The parsed list lands on both the
    top-level ``follow_up_questions`` delta and the in-place mutated
    primary-message dict so downstream readers see a consistent view.
    """
    agent = _build_agent()
    primary_response = {
        "choices": [{"message": {"content": "primary answer body"}}]
    }
    state = cast(
        KnowledgeState,
        {
            "main_response": primary_response,
            "chat_response": dict(_FOLLOW_UP_CHAT_RESPONSE),
        },
    )
    result = await agent.follow_up_post_node(state)

    assert result["follow_up_questions"] == [
        "next question one",
        "next question two",
    ]
    message = result["final_response"]["choices"][0]["message"]
    assert message["follow_up_questions"] == [
        "next question one",
        "next question two",
    ]


async def test_compiled_graph_flag_on_routes_through_shared_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph awaits the shared chat subgraph twice.

    End-to-end exercise of the prep + chat + post split: the
    compiled graph routes both the generate and follow-up chat calls
    through the single registered ``chat`` node so the patched shared
    ``CHAT_APP.ainvoke`` mock is awaited twice — once with the
    generate payload, once with the follow-up payload — while the
    legacy ``phyto_chat`` binding stays untouched. Confirms both the
    ``pending_post`` after-chat router and the back-edge from
    ``follow_up_prep_node`` to ``chat`` fire correctly.
    """
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_KNOWLEDGE_MODULE,
        legacy_response=None,
        subgraph_response=_CHAT_COMPLETION_RESPONSE,
    )

    async def fake_retrieve_node(
        _self: KnowledgeAgent, _state: KnowledgeState
    ) -> dict:
        """Stub ``retrieve_node`` so the test never hits the live backend.

        Returns the same fixed doc-list + retrieve-context fixture
        the unit tests above use so the prep node's prompt stitching
        observes a deterministic ``retrieve_context``.
        """
        return {
            "retrieved_docs": [{"title": "Plant Biology.pdf"}],
            "retrieve_context": "doc1 ... doc2 ...",
        }

    # Patch the class method BEFORE constructing the agent so the
    # original ``_build_graph`` call captures the stub instead of
    # the real backend-hitting bound method.
    monkeypatch.setattr(KnowledgeAgent, "retrieve_node", fake_retrieve_node)
    agent = _build_agent()
    initial_input = cast(
        KnowledgeInput,
        {
            "user_query": "What is photosynthesis?",
            "is_generate": True,
            "is_follow_up": True,
        },
    )
    final_state = await agent.app.ainvoke(
        initial_input,
        config={"configurable": {"thread_id": "test-thread"}},
    )

    legacy_mock.assert_not_awaited()
    assert fake_chat_app.ainvoke.await_count == 2
    payloads = [call.args[0] for call in fake_chat_app.ainvoke.await_args_list]
    assert "doc1" in payloads[0]["user_query"]
    # Second call is the follow-up prompt; it carries the primary
    # answer text the post node merged into ``main_response``.
    assert "What is photosynthesis?" in payloads[1]["user_query"]
    assert "synthesised answer" in payloads[1]["user_query"]
    message = final_state["final_response"]["choices"][0]["message"]
    assert message["doc_list"] == [{"title": "Plant Biology.pdf"}]


def test_compiled_graph_flag_on_xray_expands_chat_subgraph() -> None:
    """Flag-on graph exposes the shared chat subgraph to ``xray``.

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
    agent = _build_agent()
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
