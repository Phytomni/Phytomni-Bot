# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Graph wiring helpers for the Review agent."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ...runtime.langgraph_runner import make_async_router
from ..shared.chat_subgraph import make_chat_after_router, mount_chat_node


def _review_chat_operation(
    state: Any,
) -> tuple[str, dict[str, int]] | None:
    """Give only the report-synthesis chat call a Review presenter."""
    if state.get("pending_post") != "summary_post_node":
        return None
    dimensions = state.get("research_dimensions")
    total = len(dimensions) if isinstance(dimensions, list) else 0
    return "review.final_synthesis", {"total": total}


def wire_review_graph(
    agent: Any, workflow: StateGraph, knowledge_app: Any
) -> None:
    """Register Review prep, fan-out, chat, and settlement graph edges."""
    workflow.add_node("plan_query_prep_node", agent.plan_query_prep_node)
    workflow.add_node("plan_query_post_node", agent.plan_query_post_node)
    workflow.add_node("summary_prep_node", agent.summary_prep_node)
    workflow.add_node("summary_post_node", agent.summary_post_node)
    workflow.add_node("follow_up_prep_node", agent.follow_up_prep_node)
    workflow.add_node("follow_up_post_node", agent.follow_up_post_node)
    workflow.add_node("approval_node", agent.approval_node)
    mount_chat_node(workflow, operation_resolver=_review_chat_operation)

    if knowledge_app is None:
        raise RuntimeError(
            "unreachable: _knowledge_app must be built in __init__"
        )
    workflow.add_node("retrieve_dispatch", agent.retrieve_prepare_tasks_node)
    workflow.add_node(
        "retrieve_worker_node", agent.make_retrieve_worker_node(knowledge_app)
    )
    workflow.add_node("retrieve_reduce_node", agent.retrieve_reduce_node)
    workflow.add_conditional_edges(
        "retrieve_dispatch",
        make_async_router(agent.route_retrieve_tasks),
        ["retrieve_worker_node"],
    )
    workflow.add_edge("retrieve_worker_node", "retrieve_reduce_node")
    retrieve_in, retrieve_out = (
        "retrieve_dispatch",
        "retrieve_reduce_node",
    )

    workflow.add_node("draft_dispatch", agent.draft_prepare_tasks_node)
    workflow.add_node("draft_worker_node", agent.draft_worker_node)
    workflow.add_node("draft_reduce_node", agent.draft_reduce_node)
    workflow.add_conditional_edges(
        "draft_dispatch",
        make_async_router(agent.route_draft_tasks),
        ["draft_worker_node"],
    )
    workflow.add_edge("draft_worker_node", "draft_reduce_node")

    workflow.add_node(
        "review_results_dispatch", agent.review_results_prepare_tasks_node
    )
    workflow.add_node(
        "review_results_worker_node", agent.review_results_worker_node
    )
    workflow.add_node(
        "review_results_reduce_node", agent.review_results_reduce_node
    )
    workflow.add_conditional_edges(
        "review_results_dispatch",
        make_async_router(agent.route_review_results_tasks),
        ["review_results_worker_node"],
    )
    workflow.add_edge(
        "review_results_worker_node", "review_results_reduce_node"
    )

    workflow.add_node("revised_dispatch", agent.revised_prepare_tasks_node)
    workflow.add_node("revised_worker_node", agent.revised_worker_node)
    workflow.add_node("revised_reduce_node", agent.revised_reduce_node)
    workflow.add_conditional_edges(
        "revised_dispatch",
        make_async_router(agent.route_revised_tasks),
        ["revised_worker_node"],
    )
    workflow.add_edge("revised_worker_node", "revised_reduce_node")

    workflow.add_edge("plan_query_prep_node", "chat")
    workflow.add_edge("summary_prep_node", "chat")
    workflow.add_edge("follow_up_prep_node", "chat")
    workflow.add_conditional_edges(
        "chat",
        make_async_router(make_chat_after_router()),
        {
            "plan_query_post_node": "plan_query_post_node",
            "summary_post_node": "summary_post_node",
            "follow_up_post_node": "follow_up_post_node",
        },
    )

    workflow.add_edge(START, "plan_query_prep_node")
    workflow.add_edge("plan_query_post_node", retrieve_in)
    workflow.add_edge(retrieve_out, "draft_dispatch")
    workflow.add_edge("draft_reduce_node", "review_results_dispatch")
    workflow.add_edge("review_results_reduce_node", "revised_dispatch")
    workflow.add_edge("revised_reduce_node", "summary_prep_node")
    workflow.add_edge("summary_post_node", "approval_node")
    workflow.add_conditional_edges(
        "approval_node",
        make_async_router(agent.route_after_approval),
        {
            "follow_up_prep_node": "follow_up_prep_node",
            "summary_prep_node": "summary_prep_node",
        },
    )
    workflow.add_edge("follow_up_post_node", END)


__all__ = ["wire_review_graph"]
