# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Builder for the parallel-dispatch StateGraph pattern.

Classes: ParallelDispatchSpec.
Functions: build_parallel_dispatch_graph.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

__all__ = [
    "ParallelDispatchSpec",
    "build_parallel_dispatch_graph",
]


@dataclass(frozen=True)
class ParallelDispatchSpec:
    """Inputs needed to compile a parallel-dispatch LangGraph workflow.

    Attributes:
        state_class: TypedDict subclass used as the graph state schema.
        prepare_node: Async node callable that builds the task list on
            state. Registered as ``"prepare_tasks_node"``.
        work_node: Async node callable invoked once per dispatched task.
        route_fn: Callable that converts state into a list of LangGraph
            ``Send`` commands targeted at ``work_node_name``.
        work_node_name: Node name used for the worker (matches the
            existing per-domain convention, e.g. ``"design_node"``).
        extract_node: Optional async node callable run before
            ``prepare_tasks_node`` (used by workflows that extract goals
            or context from input before task preparation).
        extract_node_name: Node name used for ``extract_node`` when
            present. Defaults to ``"extract_node"``.
    """

    state_class: type
    prepare_node: Callable[..., Any]
    work_node: Callable[..., Any]
    route_fn: Callable[..., Any]
    work_node_name: str
    extract_node: Optional[Callable[..., Any]] = None
    extract_node_name: str = "extract_node"


def build_parallel_dispatch_graph(
    spec: ParallelDispatchSpec,
    checkpointer: Optional[Any] = None,
) -> Any:
    """Compile the standard parallel-dispatch LangGraph workflow.

    Layout (with ``spec.extract_node`` set):

        START → extract_node → prepare_tasks_node
                                  ↓ conditional (Send list)
                                work_node → END

    Layout (without ``spec.extract_node``):

        START → prepare_tasks_node
                  ↓ conditional (Send list)
                work_node → END

    Args:
        spec: Configuration describing the state class, node callables,
            route function, and optional extract-node prefix.
        checkpointer: Optional LangGraph checkpointer forwarded to
            ``StateGraph.compile``.

    Returns:
        Compiled LangGraph application ready for ``ainvoke``.
    """
    workflow: StateGraph = StateGraph(spec.state_class)
    workflow.add_node("prepare_tasks_node", spec.prepare_node)
    workflow.add_node(spec.work_node_name, spec.work_node)
    if spec.extract_node is not None:
        workflow.add_node(spec.extract_node_name, spec.extract_node)
        workflow.add_edge(START, spec.extract_node_name)
        workflow.add_edge(spec.extract_node_name, "prepare_tasks_node")
    else:
        workflow.add_edge(START, "prepare_tasks_node")
    workflow.add_conditional_edges(
        "prepare_tasks_node",
        spec.route_fn,
        [spec.work_node_name],
    )
    workflow.add_edge(spec.work_node_name, END)
    return workflow.compile(checkpointer=checkpointer)
