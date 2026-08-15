# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cancellation coverage for request-owned graph stream iteration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, TypedDict

import anyio
import pytest
from langgraph.graph import END, START, StateGraph

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.server


class _GraphState(TypedDict):
    """Minimal state schema for the cancellation-only graph."""

    user_query: str


async def test_graph_stream_cancellation_closes_active_nested_anext() -> None:
    """Cancelling a graph consumer closes its active nested graph iterator."""
    both_started = asyncio.Event()
    active = 0
    cancelled = 0
    leaf_tasks: list[asyncio.Task[None]] = []

    async def wait_forever() -> None:
        """Record one active fan-out leaf until graph cancellation arrives."""
        nonlocal active, cancelled
        task = asyncio.current_task()
        assert task is not None
        leaf_tasks.append(task)
        active += 1
        if active == 2:
            both_started.set()
        try:
            await anyio.sleep_forever()
        except asyncio.CancelledError:
            cancelled += 1
            raise
        finally:
            active -= 1

    async def retrieve_node(_state: _GraphState) -> dict[str, Any]:
        """Fan out two active leaves beneath the compiled graph stream."""
        await asyncio.gather(wait_forever(), wait_forever())
        return {}

    workflow = StateGraph(_GraphState)
    workflow.add_node("retrieve_node", retrieve_node)
    workflow.add_edge(START, "retrieve_node")
    workflow.add_edge("retrieve_node", END)

    events = mcp_app._stream_graph_agent(
        workflow.compile(),
        {"user_query": "cancel"},
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        run_id="run-graph-cancel",
        dialogue_id=None,
    )
    assert (await anext(events)).type == "RunStarted"

    async def consume_next_event() -> None:
        """Drive the second event so the nested graph ``anext`` is active."""
        await anext(events)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume_next_event)
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        tasks.cancel_scope.cancel()

    observed_active = active
    observed_cancelled = cancelled
    observed_tasks_done = all(task.done() for task in leaf_tasks)
    with suppress(RuntimeError):
        await events.aclose()
    for task in leaf_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*leaf_tasks, return_exceptions=True)

    assert observed_active == 0
    assert observed_cancelled == 2
    assert observed_tasks_done
