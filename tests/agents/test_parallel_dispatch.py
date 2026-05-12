# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared parallel-dispatch StateGraph builder.

Covers the with-extract-node and without-extract-node compile shapes
plus a toy end-to-end run that confirms the Send-based dispatch path
fans tasks out to the worker node.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, List, TypedDict

import pytest
from langgraph.types import Send

from mcp_server_phytomni.agents.shared.parallel_dispatch import (
    ParallelDispatchSpec,
    build_parallel_dispatch_graph,
)

pytestmark = pytest.mark.agent


class _ToyState(TypedDict, total=False):
    """Toy state for the parallel-dispatch end-to-end test."""

    tasks: List[Any]
    task_index: int
    results: Annotated[List[Any], operator.add]


async def _toy_prepare(state: _ToyState) -> dict[str, Any]:
    """Seed three tasks on state.

    Args:
        state: Current toy state.

    Returns:
        State update with three task payloads.
    """
    assert state is not None
    return {"tasks": [{"value": i} for i in range(3)], "results": []}


def _toy_route(state: _ToyState) -> list[Send]:
    """Fan tasks out to the worker node.

    Args:
        state: Current toy state with prepared tasks.

    Returns:
        Send commands for each prepared task.
    """
    return [
        Send("work", {"task_index": i, **task})
        for i, task in enumerate(state.get("tasks", []))
    ]


async def _toy_work(state: _ToyState) -> dict[str, Any]:
    """Append the squared task index to the results accumulator.

    Args:
        state: Single-task state slice produced by the Send dispatch.

    Returns:
        State update appending the per-task result.
    """
    idx = state.get("task_index", 0)
    return {"results": [idx * idx]}


async def test_build_parallel_dispatch_graph_runs_three_tasks_in_parallel():
    """Verify the no-extract layout dispatches every prepared task.

    Returns:
        None after the merged result list assertion passes.
    """
    spec = ParallelDispatchSpec(
        state_class=_ToyState,
        prepare_node=_toy_prepare,
        work_node=_toy_work,
        route_fn=_toy_route,
        work_node_name="work",
    )
    app = build_parallel_dispatch_graph(spec)

    final_state = await app.ainvoke({})

    assert sorted(final_state["results"]) == [0, 1, 4]


async def _toy_extract(state: _ToyState) -> dict[str, Any]:
    """Mark that the extract node ran before task preparation.

    Args:
        state: Current toy state.

    Returns:
        State update recording the extract pass.
    """
    assert state is not None
    return {"results": [-1]}


async def test_build_parallel_dispatch_graph_runs_extract_node_first():
    """Verify the with-extract layout runs extract before prepare.

    Returns:
        None after the extract sentinel and per-task assertions pass.
    """
    spec = ParallelDispatchSpec(
        state_class=_ToyState,
        prepare_node=_toy_prepare,
        work_node=_toy_work,
        route_fn=_toy_route,
        work_node_name="work",
        extract_node=_toy_extract,
        extract_node_name="extract",
    )
    app = build_parallel_dispatch_graph(spec)

    final_state = await app.ainvoke({})

    assert -1 in final_state["results"]
    assert sorted(v for v in final_state["results"] if v >= 0) == [0, 1, 4]
