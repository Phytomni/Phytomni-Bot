# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compile the evolution LangGraph workflow.

``build_evolution_graph`` assembles the
``resolve_target_taxids → submit_evolution_task`` pipeline with an
explicit conditional edge after resolution so the failure path
short-circuits to END. ``StateGraph`` carries separate
``input_schema`` / ``output_schema`` so parent graphs mounting
the compiled result see a narrow public contract.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .graph import (
    resolve_target_taxids_node,
    route_after_resolve,
    submit_evolution_task_node,
)
from .state import EvolutionInput, EvolutionOutput, EvolutionState


def build_evolution_graph(checkpointer: Any | None = None) -> Any:
    """Return a compiled evolution LangGraph app.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g.
            :class:`langgraph.checkpoint.memory.MemorySaver`). When
            ``None``, the compiled app runs without persistent
            state — the default for stateless evolution turns.

    Returns:
        ``CompiledStateGraph`` ready for ``ainvoke`` / ``get_graph``.
        Both ``input_schema=EvolutionInput`` and
        ``output_schema=EvolutionOutput`` surface the public IO
        contract in ``get_graph()`` metadata; the conditional edge
        after ``resolve_target_taxids_node`` carries an explicit
        ``path_map`` so the Mermaid renderer draws both branches.
    """
    workflow = StateGraph(
        state_schema=EvolutionState,
        input_schema=EvolutionInput,
        output_schema=EvolutionOutput,
    )
    workflow.add_node("resolve_target_taxids_node", resolve_target_taxids_node)
    workflow.add_node("submit_evolution_task_node", submit_evolution_task_node)
    workflow.add_edge(START, "resolve_target_taxids_node")
    workflow.add_conditional_edges(
        "resolve_target_taxids_node",
        route_after_resolve,
        {
            "submit_evolution_task_node": "submit_evolution_task_node",
            "__end__": END,
        },
    )
    workflow.add_edge("submit_evolution_task_node", END)
    return workflow.compile(checkpointer=checkpointer)
