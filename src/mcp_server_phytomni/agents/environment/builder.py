# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compile the environment VCI LangGraph workflow.

``build_environment_graph`` assembles the
``extract_region_codes → submit_vci_task`` pipeline with an
explicit conditional edge after extraction so the failure path
short-circuits to END. ``StateGraph`` is constructed with separate
``input_schema`` / ``output_schema`` so parent graphs mounting
the compiled result see a narrow public contract.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ...runtime.langgraph_runner import make_async_router
from .graph import (
    extract_region_codes_node,
    route_after_extract,
    submit_vci_task_node,
)
from .state import EnvironmentInput, EnvironmentOutput, EnvironmentState


def build_environment_graph(checkpointer: Any | None = None) -> Any:
    """Return a compiled environment VCI LangGraph app.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g.
            :class:`langgraph.checkpoint.memory.MemorySaver`). When
            ``None``, the compiled app runs without persistent
            state — the default for stateless region analysis turns.

    Returns:
        ``CompiledStateGraph`` ready for ``ainvoke`` / ``get_graph``.
        Both ``input_schema=EnvironmentInput`` and
        ``output_schema=EnvironmentOutput`` surface the public IO
        contract in ``get_graph()`` metadata; the conditional edge
        after ``extract_region_codes_node`` carries an explicit
        ``path_map`` so the Mermaid renderer draws both branches by
        name.
    """
    workflow = StateGraph(
        state_schema=EnvironmentState,
        input_schema=EnvironmentInput,
        output_schema=EnvironmentOutput,
    )
    workflow.add_node("extract_region_codes_node", extract_region_codes_node)
    workflow.add_node("submit_vci_task_node", submit_vci_task_node)
    workflow.add_edge(START, "extract_region_codes_node")
    workflow.add_conditional_edges(
        "extract_region_codes_node",
        make_async_router(route_after_extract),
        {"submit_vci_task_node": "submit_vci_task_node", "__end__": END},
    )
    workflow.add_edge("submit_vci_task_node", END)
    return workflow.compile(checkpointer=checkpointer)
