# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cross-restart persistence: a pause point survives a new saver."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from mcp_server_phytomni.runtime.checkpoint_backend import (
    build_default_checkpointer,
)
from mcp_server_phytomni.runtime.langgraph_runner import build_runnable_config
from mcp_server_phytomni.runtime.resume import aresume_graph, detect_interrupt


class _RestartState(TypedDict):
    """State for the restart-persistence graph."""

    value: str
    decision: NotRequired[dict[str, Any]]
    final: NotRequired[str]


def _build_restart_app(checkpointer: AsyncSqliteSaver) -> Any:
    """Build a graph that pauses once, then finalizes on approval."""

    async def gate(state: _RestartState) -> dict[str, Any]:
        decision = interrupt({"draft": state["value"]})
        return {"decision": decision}

    async def finalize(state: _RestartState) -> dict[str, str]:
        decision = state.get("decision", {})
        approved = decision.get("approved")
        return {"final": "ok" if approved else "redo"}

    graph = StateGraph(_RestartState)
    graph.add_node("gate", gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "gate")
    graph.add_edge("gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


async def _close_saver(saver: AsyncSqliteSaver) -> None:
    """Close the underlying SQLite connection after graph use."""
    await saver.conn.close()


@pytest.mark.asyncio
async def test_pause_point_survives_fresh_sqlite_saver(tmp_path) -> None:
    """A pause written by one saver resumes through a fresh saver."""
    db_path = str(tmp_path / "checkpoints.db")
    thread_id = "restart-thread"
    saver_a = build_default_checkpointer(db_path)

    try:
        app_a = _build_restart_app(saver_a)
        paused = await app_a.ainvoke(
            {"value": "draft-text"},
            config=build_runnable_config(thread_id),
        )
        info = detect_interrupt(paused, thread_id)
        assert info is not None
        assert info["draft"] == {"draft": "draft-text"}
    finally:
        await _close_saver(saver_a)

    saver_b = build_default_checkpointer(db_path)
    try:
        app_b = _build_restart_app(saver_b)
        final = await aresume_graph(app_b, thread_id, {"approved": True})
        assert final["final"] == "ok"
    finally:
        await _close_saver(saver_b)
