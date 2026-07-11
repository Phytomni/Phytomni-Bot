# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the ReviewAgent approval node and state channels."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.runtime.langgraph_runner import (
    build_runnable_config,
    make_async_router,
)
from mcp_server_phytomni.runtime.resume import (
    aresume_graph,
    detect_interrupt,
)


class _ApprovalState(TypedDict, total=False):
    """Minimal state schema for the approval-node isolation tests."""

    summary_content: str
    approval_decision: dict[str, Any]


class _MinimalGraphState(TypedDict, total=False):
    """Minimal state schema for the pause-then-resume graph test."""

    summary_content: str
    approval_decision: dict[str, Any]
    approval_pending: bool
    final_response: dict[str, Any]


async def _stub_follow_up(
    state: _MinimalGraphState,
) -> dict[str, Any]:
    """Stand in for ``follow_up_prep_node`` in the minimal graph."""
    del state
    return {"final_response": {"answer": "finalized"}}


async def _stub_summary_prep(
    state: _MinimalGraphState,
) -> dict[str, Any]:
    """Stand in for ``summary_prep_node`` in the minimal graph."""
    del state
    return {"summary_content": "REGENERATED"}


def test_state_has_approval_channels() -> None:
    """DeepResearchState declares the approval channels."""
    annotations = DeepResearchState.__annotations__
    assert "approval_pending" in annotations
    assert "approval_decision" in annotations
    assert "a2ui_round" in annotations


@pytest.mark.asyncio
async def test_approval_node_interrupts_with_summary() -> None:
    """approval_node pauses the graph, surfacing the summary draft.

    ``interrupt()`` requires a live Pregel runnable context (checkpoint
    namespace + resume scratchpad) to reach its ``GraphInterrupt``
    raise, so the node is driven through a minimal single-node graph
    with a real checkpointer -- mirroring
    ``tests/agents/test_resume_kernel.py``'s ``_build_stub_app``
    pattern -- rather than called bare. ``ainvoke`` catches the
    ``GraphInterrupt`` Pregel-internally and surfaces it as
    ``__interrupt__`` state, which ``detect_interrupt`` reads.
    """
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    graph = StateGraph(_ApprovalState)
    graph.add_node("approval_node", agent.approval_node)
    graph.add_edge(START, "approval_node")
    graph.add_edge("approval_node", END)
    app = graph.compile(checkpointer=MemorySaver())

    final = await app.ainvoke(
        {"summary_content": "DRAFT REVIEW", "approval_decision": {}},
        config=build_runnable_config("approval-1"),
    )
    info = detect_interrupt(final)
    assert info is not None
    # The interrupt payload carries the draft for the human to review.
    assert info["draft"] == {"draft": "DRAFT REVIEW"}


@pytest.mark.asyncio
async def test_approval_node_returns_decision_on_resume() -> None:
    """When interrupt() yields a decision, the node records it."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    state = cast(
        DeepResearchState,
        {"summary_content": "DRAFT", "approval_decision": {}},
    )
    decision = {"approved": True, "edits": None}
    with patch(
        "mcp_server_phytomni.agents.review.agent.interrupt",
        return_value=decision,
    ):
        result = await DeepResearchAgent.approval_node(agent, state)
    assert result["approval_decision"] == decision
    assert result["approval_pending"] is False
    assert result["a2ui_round"] == 1


def test_route_after_approval_finalizes_when_approved() -> None:
    """route_after_approval sends approved runs to follow_up."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    approved = cast(
        DeepResearchState, {"approval_decision": {"approved": True}}
    )
    rejected = cast(
        DeepResearchState,
        {"approval_decision": {"approved": False}, "a2ui_round": 1},
    )
    assert (
        DeepResearchAgent.route_after_approval(agent, approved)
        == "follow_up_prep_node"
    )
    assert (
        DeepResearchAgent.route_after_approval(agent, rejected)
        == "summary_prep_node"
    )


def test_route_after_approval_forces_follow_up_at_max_round() -> None:
    """Reject at a2ui_round=2 forces follow_up instead of redraft."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    state = cast(
        DeepResearchState,
        {
            "approval_decision": {"approved": False},
            "a2ui_round": 2,
        },
    )
    assert (
        DeepResearchAgent.route_after_approval(agent, state)
        == "follow_up_prep_node"
    )


def test_route_after_approval_form_submit_follows_up() -> None:
    """Form submit fields continue to follow_up without redraft."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    state = cast(
        DeepResearchState,
        {
            "approval_decision": {
                "approved": True,
                "fields": {"gene_id": "ATG1"},
            },
            "a2ui_round": 1,
        },
    )
    assert (
        DeepResearchAgent.route_after_approval(agent, state)
        == "follow_up_prep_node"
    )


@pytest.mark.asyncio
async def test_minimal_graph_pauses_then_resumes() -> None:
    """The real approval_node pauses, then resumes to follow_up."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)

    graph = StateGraph(_MinimalGraphState)
    graph.add_node("approval_node", agent.approval_node)
    graph.add_node("follow_up_prep_node", _stub_follow_up)
    graph.add_node("summary_prep_node", _stub_summary_prep)
    graph.add_edge(START, "approval_node")
    graph.add_conditional_edges(
        "approval_node",
        make_async_router(agent.route_after_approval),
        {
            "follow_up_prep_node": "follow_up_prep_node",
            "summary_prep_node": "summary_prep_node",
        },
    )
    graph.add_edge("follow_up_prep_node", END)
    graph.add_edge("summary_prep_node", "approval_node")
    app = graph.compile(checkpointer=MemorySaver())

    paused = await app.ainvoke(
        {"summary_content": "DRAFT", "approval_decision": {}},
        config=build_runnable_config("rev-1"),
    )
    info = detect_interrupt(paused, "rev-1")
    assert info is not None
    assert info["draft"] == {"draft": "DRAFT"}

    final = await aresume_graph(app, "rev-1", {"approved": True})
    assert final["final_response"] == {"answer": "finalized"}
