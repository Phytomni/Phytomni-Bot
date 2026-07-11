# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compile the Chat A2UI confirm/form/choice interrupt LangGraph workflow.

``build_chat_a2ui_graph`` assembles the
``prepare_context → a2ui_prepare_surface → a2ui_confirm →
(a2ui_apply_decision → generate → follow_up | cancel)`` pipeline, then
routes through ``a2ui_after_work`` so a bounded N=2 re-enter may remint
a fresh surface. The shared ``_build_chat_graph`` subgraph stays
interrupt-free; HTTP A2UI callers mount this dedicated app instead.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ...runtime.langgraph_runner import make_async_router
from .a2ui_graph import (
    ChatA2uiOutput,
    ChatA2uiState,
    a2ui_after_work_node,
    a2ui_apply_decision_node,
    a2ui_cancel_node,
    a2ui_confirm_node,
    a2ui_prepare_surface_node,
    a2ui_reenter_node,
    route_after_a2ui_confirm,
    route_after_a2ui_turn,
)
from .graph import (
    follow_up_node,
    generate_node,
    prepare_context_node,
    route_after_generate,
)
from .state import ChatInput


def build_chat_a2ui_graph(checkpointer: Any | None = None) -> Any:
    """Return a compiled Chat A2UI confirm LangGraph app.

    Args:
        checkpointer: LangGraph checkpointer (e.g.
            :class:`langgraph.checkpoint.memory.MemorySaver`). Required
            for pause/resume across the confirm interrupt.

    Returns:
        ``CompiledStateGraph`` ready for ``ainvoke`` / ``aresume_graph``.
    """
    workflow = StateGraph(
        state_schema=ChatA2uiState,
        input_schema=ChatInput,
        output_schema=ChatA2uiOutput,
    )
    workflow.add_node("prepare_context_node", prepare_context_node)
    workflow.add_node(
        "a2ui_prepare_surface_node",
        a2ui_prepare_surface_node,
    )
    workflow.add_node("a2ui_confirm_node", a2ui_confirm_node)
    workflow.add_node(
        "a2ui_apply_decision_node",
        a2ui_apply_decision_node,
    )
    workflow.add_node("generate_node", generate_node)
    workflow.add_node("follow_up_node", follow_up_node)
    workflow.add_node("a2ui_after_work_node", a2ui_after_work_node)
    workflow.add_node("a2ui_reenter_node", a2ui_reenter_node)
    workflow.add_node("a2ui_cancel_node", a2ui_cancel_node)
    workflow.add_edge(START, "prepare_context_node")
    workflow.add_edge(
        "prepare_context_node",
        "a2ui_prepare_surface_node",
    )
    workflow.add_edge(
        "a2ui_prepare_surface_node",
        "a2ui_confirm_node",
    )
    workflow.add_conditional_edges(
        "a2ui_confirm_node",
        make_async_router(route_after_a2ui_confirm),
        {
            "a2ui_apply_decision_node": "a2ui_apply_decision_node",
            "a2ui_cancel_node": "a2ui_cancel_node",
        },
    )
    workflow.add_edge("a2ui_apply_decision_node", "generate_node")
    workflow.add_conditional_edges(
        "generate_node",
        make_async_router(route_after_generate),
        {
            "follow_up_node": "follow_up_node",
            "__end__": "a2ui_after_work_node",
        },
    )
    workflow.add_edge("follow_up_node", "a2ui_after_work_node")
    workflow.add_conditional_edges(
        "a2ui_after_work_node",
        make_async_router(route_after_a2ui_turn),
        {
            "a2ui_reenter_node": "a2ui_reenter_node",
            "__end__": END,
        },
    )
    workflow.add_edge(
        "a2ui_reenter_node",
        "a2ui_prepare_surface_node",
    )
    workflow.add_edge("a2ui_cancel_node", END)
    return workflow.compile(checkpointer=checkpointer)
