# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compile the chat LangGraph workflow.

``_build_chat_graph`` assembles the ``prepare_context → generate →
follow_up`` pipeline with an explicit conditional edge after
``generate_node``. ``StateGraph`` is constructed with separate
``input_schema`` / ``output_schema`` so parent graphs that mount the
result via ``parent.add_node("chat", child_app)`` see a narrow public
contract while internal nodes still operate on the full ``ChatState``.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ...runtime.langgraph_runner import make_async_router
from .graph import (
    follow_up_node,
    generate_node,
    prepare_context_node,
    route_after_generate,
)
from .state import ChatInput, ChatOutput, ChatState


def _build_chat_graph(checkpointer: Any | None = None) -> Any:
    """Return a compiled chat LangGraph app.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g.
            :class:`langgraph.checkpoint.memory.MemorySaver`). When
            ``None``, the compiled app runs without persistent state
            — the default for stateless chat turns.

    Returns:
        ``CompiledStateGraph`` ready for ``ainvoke`` / ``get_graph``.
        Both ``input_schema=ChatInput`` and ``output_schema=ChatOutput``
        are wired so the public IO contract surfaces in
        ``get_graph()`` metadata, and ``add_conditional_edges`` after
        ``generate_node`` carries an explicit ``path_map`` so the
        Mermaid renderer draws both branches by name.
    """
    workflow = StateGraph(
        state_schema=ChatState,
        input_schema=ChatInput,
        output_schema=ChatOutput,
    )
    workflow.add_node("prepare_context_node", prepare_context_node)
    workflow.add_node("generate_node", generate_node)
    workflow.add_node("follow_up_node", follow_up_node)
    workflow.add_edge(START, "prepare_context_node")
    workflow.add_edge("prepare_context_node", "generate_node")
    workflow.add_conditional_edges(
        "generate_node",
        make_async_router(route_after_generate),
        {"follow_up_node": "follow_up_node", "__end__": END},
    )
    workflow.add_edge("follow_up_node", END)
    return workflow.compile(checkpointer=checkpointer)
