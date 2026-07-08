# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the protocol-agnostic resume kernel."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from mcp_server_phytomni.runtime.langgraph_runner import (
    build_runnable_config,
)
from mcp_server_phytomni.runtime.resume import (
    NoCheckpointError,
    aresume_graph,
    detect_interrupt,
)


class _State(TypedDict):
    value: str
    decision: NotRequired[dict[str, Any]]
    final: NotRequired[str]


def _build_stub_app() -> Any:
    """A minimal graph that interrupts once, then finalizes on resume."""

    async def gate(state: _State) -> dict[str, Any]:
        decision = interrupt({"draft": state["value"]})
        return {"decision": decision}

    async def finalize(state: _State) -> dict[str, Any]:
        decision = state.get("decision", {})
        approved = decision.get("approved")
        return {"final": "ok" if approved else "redo"}

    graph = StateGraph(_State)
    graph.add_node("gate", gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "gate")
    graph.add_edge("gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_detect_interrupt_reads_paused_draft() -> None:
    """detect_interrupt surfaces the draft payload from a paused run."""
    app = _build_stub_app()
    final = await app.ainvoke(
        {"value": "draft-text"},
        config=build_runnable_config("t-1"),
    )
    info = detect_interrupt(final)
    assert info is not None
    assert info["draft"] == {"draft": "draft-text"}


@pytest.mark.asyncio
async def test_aresume_graph_finalizes_on_approval() -> None:
    """aresume_graph drives the paused graph to its terminal state."""
    app = _build_stub_app()
    await app.ainvoke(
        {"value": "draft-text"},
        config=build_runnable_config("t-2"),
    )
    result = await aresume_graph(app, "t-2", {"approved": True})
    assert result["final"] == "ok"


@pytest.mark.asyncio
async def test_aresume_graph_unknown_thread_raises() -> None:
    """Resuming a thread with no stored pause point raises cleanly."""
    app = _build_stub_app()
    with pytest.raises(NoCheckpointError):
        await aresume_graph(app, "never-started", {"approved": True})
