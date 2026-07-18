# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the chat LangGraph structural assembly.

Pins the chat subgraph's node set, the START edge, the prepare→
generate edge, the conditional edge after ``generate_node`` with an
explicit ``path_map`` covering both branches, and the
``input_schema`` / ``output_schema`` wiring that lets parent graphs
mount this compiled app via ``parent.add_node`` against a narrow
public contract.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.chat.builder import build_chat_graph
from mcp_server_phytomni.agents.chat.graph import route_after_generate
from mcp_server_phytomni.agents.chat.state import (
    ChatInput,
    ChatOutput,
    ChatState,
)

pytestmark = pytest.mark.agent


def test_build_chat_graph_compiles_with_three_nodes() -> None:
    """The compiled chat graph carries exactly three non-boundary nodes.

    Pins the prepare_context / generate / follow_up split called out
    by the plan. If a future refactor inlines one of the three into
    another node, this test fails first so the rebalance is
    intentional (and the manifest snapshot / visualization rendering
    that depend on the three-node count update in the same diff).
    """
    app = build_chat_graph()
    nodes = set(app.get_graph().nodes)
    non_boundary = nodes - {"__start__", "__end__"}
    assert non_boundary == {
        "prepare_context_node",
        "generate_node",
        "follow_up_node",
    }


def test_chat_graph_start_edge_targets_prepare_context() -> None:
    """``__start__`` flows into ``prepare_context_node`` first.

    Pins ordering: file-download / upload-context conversion must
    happen before the LLM call, otherwise ``generate_node`` would
    see the raw user_query and the OBS-attached files would never
    reach the model.
    """
    app = build_chat_graph()
    edges = app.get_graph().edges
    start_targets = {e.target for e in edges if e.source == "__start__"}
    assert start_targets == {"prepare_context_node"}


def test_chat_graph_prepare_edges_into_generate() -> None:
    """``prepare_context_node`` unconditionally feeds ``generate_node``.

    Pins that the prepare→generate transition is a fixed (non-
    conditional) edge: there is no reason to skip the LLM call once
    context has been prepared, so the edge must not carry a router.
    """
    app = build_chat_graph()
    prepare_edges = [
        e for e in app.get_graph().edges if e.source == "prepare_context_node"
    ]
    assert len(prepare_edges) == 1
    assert prepare_edges[0].target == "generate_node"
    assert prepare_edges[0].conditional is False


def test_chat_graph_conditional_edge_after_generate() -> None:
    """``generate_node`` branches to ``follow_up_node`` or ``__end__``.

    Pins the conditional edge with an explicit ``path_map`` over
    both branches. Without explicit path_map, the LangGraph Mermaid
    renderer cannot label the branches by name — they collapse into
    a single anonymous fork that hides which call shape (chat vs
    chat-with-follow) is in play.
    """
    app = build_chat_graph()
    generate_edges = [
        e for e in app.get_graph().edges if e.source == "generate_node"
    ]
    targets = {e.target for e in generate_edges}
    conditional_flags = {e.conditional for e in generate_edges}
    assert "follow_up_node" in targets
    assert "__end__" in targets
    assert conditional_flags == {True}


def test_route_after_generate_default_routes_to_follow_up() -> None:
    """Default ``chat_kwargs`` routes through ``follow_up_node``.

    Pins backward compatibility with ``phyto_chat_with_follow``: when
    a caller does not opt out, the legacy follow-up question
    generation still runs.
    """
    state: ChatState = {"user_query": "anything"}
    assert route_after_generate(state) == "follow_up_node"


def test_route_after_generate_opt_out_routes_to_end() -> None:
    """``with_follow_up=False`` short-circuits to ``__end__``.

    Pins the switch that lets ``phyto_chat`` (no-follow) and
    ``phyto_chat_with_follow`` (with-follow) share one compiled
    subgraph. Without this branch, callers would need two compiled
    apps just to express the on/off variant.
    """
    state: ChatState = {
        "user_query": "anything",
        "chat_kwargs": {"with_follow_up": False},
    }
    assert route_after_generate(state) == "__end__"


def test_chat_graph_follow_up_edges_into_end() -> None:
    """``follow_up_node`` is terminal.

    Pins that follow-up question generation is the last step on its
    branch; no third LLM call sneaks in after.
    """
    app = build_chat_graph()
    follow_edges = [
        e for e in app.get_graph().edges if e.source == "follow_up_node"
    ]
    assert len(follow_edges) == 1
    assert follow_edges[0].target == "__end__"


def test_chat_graph_exposes_input_and_output_schemas() -> None:
    """The compiled graph carries narrow input/output schemas.

    Pins that a parent graph mounting this subgraph sees ``ChatInput``
    fields on the input port and ``ChatOutput`` fields on the output
    port — the full ``ChatState`` (including the intermediate
    ``upload_context``) is hidden from parents. ``StateGraph``
    records these classes on the compiled app so loaders can read
    them without re-importing the subgraph module.
    """
    app = build_chat_graph()
    assert app.builder.input_schema is ChatInput
    assert app.builder.output_schema is ChatOutput
    assert app.builder.state_schema is ChatState
