# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ReviewAgent's three single-shot chat sites.

The three single-shot chat sites (``plan_query`` / ``summary`` /
``follow_up``) route through prep + post pairs surrounding a single
shared chat node registered via ``mount_chat_node`` from the
``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.review.state import DeepResearchState
from tests.support.review_fan_out import (
    build_review_agent,
    follow_up_state,
    plan_query_state,
    review_summary_state,
)
from tests.support.subgraph_fakes import (
    REVIEW_CHAT_MOUNT_TOPOLOGY,
    assert_chat_mount_topology,
    assert_subgraph_prefixes,
)

pytestmark = pytest.mark.agent


def test_compiled_graph_preserves_chat_mount_topology() -> None:
    """Pin Review's public schema, routes, xray, and checkpointer contract."""
    agent = build_review_agent()
    assert_chat_mount_topology(
        agent.app,
        agent.checkpointer,
        REVIEW_CHAT_MOUNT_TOPOLOGY,
    )

    assert (
        agent.route_after_approval(
            cast(DeepResearchState, {"approval_decision": {"approved": True}})
        )
        == "follow_up_prep_node"
    )
    assert (
        agent.route_after_approval(
            cast(DeepResearchState, {"approval_decision": {}})
        )
        == "summary_prep_node"
    )


# ---------------------------------------------------------------------------
# Prep nodes stage ``chat_payload`` + ``pending_post``.
# ---------------------------------------------------------------------------


async def test_plan_query_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for plan_query."""
    agent = build_review_agent()
    state = cast(DeepResearchState, plan_query_state())
    result = await agent.plan_query_prep_node(state)

    assert result["pending_post"] == "plan_query_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "How does photosynthesis work?" in chat_payload["user_query"]
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 19
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-review"
    )
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_summary_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for summary."""
    agent = build_review_agent()
    state = cast(DeepResearchState, review_summary_state())
    result = await agent.summary_prep_node(state)

    assert result["pending_post"] == "summary_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "Photosynthesis" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 19
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-review"
    )
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_follow_up_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for follow_up."""
    agent = build_review_agent()
    state = cast(DeepResearchState, follow_up_state())
    result = await agent.follow_up_prep_node(state)

    assert result["pending_post"] == "follow_up_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "Photosynthesis" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 19
    assert chat_payload["chat_kwargs"]["relay_timeout_profile"] == (
        "phyto-review"
    )
    assert chat_payload["chat_kwargs"]["with_follow_up"] is True


# ---------------------------------------------------------------------------
# Flag-on post nodes parse ``state['chat_response']`` into the legacy delta.
# ---------------------------------------------------------------------------


async def test_plan_query_post_node_parses_chat_response() -> None:
    """Post node parses ``chat_response`` into the plan_query delta."""
    agent = build_review_agent()
    state = cast(
        DeepResearchState,
        {
            "chat_response": {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"Research_dimensions":'
                                ' ["dim1", "dim2", "dim3"]}'
                            )
                        }
                    }
                ]
            }
        },
    )
    result = await agent.plan_query_post_node(state)

    assert result["research_dimensions"] == ["dim1", "dim2", "dim3"]


async def test_summary_post_node_parses_chat_response() -> None:
    """Post node parses ``chat_response`` into ``summary_content``."""
    agent = build_review_agent()
    state = cast(
        DeepResearchState,
        {
            "chat_response": {
                "choices": [
                    {"message": {"content": "Combined review `text`."}}
                ]
            }
        },
    )
    result = await agent.summary_post_node(state)

    assert result["summary_content"] == "Combined review text."


async def test_follow_up_post_node_parses_chat_response() -> None:
    """Post node assembles ``final_response`` from the follow-up response."""
    agent = build_review_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "A review of photosynthesis.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
            "chat_response": {
                "choices": [
                    {"message": {"content": "1. How do plants capture light?"}}
                ]
            },
        },
    )
    result = await agent.follow_up_post_node(state)

    assert "final_response" in result
    final = result["final_response"]
    assert final.get("choices")
    message = final["choices"][0]["message"]
    assert "follow_up_questions" in message


async def test_follow_up_prep_to_post_preserves_doc_list() -> None:
    """Prep computes ``ordered_doc_list`` once; post reads it from state.

    Regression for the double-``_renumber_citations`` bug: prep
    renumbers ``summary_content`` to ``[document:N]`` and forwards the
    ordered references under ``ordered_doc_list``. If post re-ran
    ``_renumber_citations`` on the already-renumbered text it would
    recover an empty list (the citation pattern does not match
    ``[document:N]``), silently dropping every reference from
    ``final_response``. Drives the wired prep -> post sequence with
    NON-EMPTY doc lists, which the other follow_up tests omit.
    """
    agent = build_review_agent()
    prep_state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "Finding A [document 001] and B [S1-001].",
            "all_raw_doc_list": [
                {"doc_id": "document 001", "title": "Paper A"}
            ],
            "add_doc_list": [
                {"doc_id": "add document S1-001", "title": "Supp B"}
            ],
        },
    )
    prep = await agent.follow_up_prep_node(prep_state)

    assert prep["summary_content"] == (
        "Finding A [document:1] and B [document:2]."
    )
    assert [doc["title"] for doc in prep["ordered_doc_list"]] == [
        "Paper A",
        "Supp B",
    ]

    post_state = cast(
        DeepResearchState,
        {
            **prep_state,
            **prep,
            "chat_response": {
                "choices": [
                    {"message": {"content": '["How is light captured?"]'}}
                ]
            },
        },
    )
    result = await agent.follow_up_post_node(post_state)

    message = result["final_response"]["choices"][0]["message"]
    assert [doc["title"] for doc in message["doc_list"]] == [
        "Paper A",
        "Supp B",
    ]
    assert "[document:1]" in message["content"]
    assert message["follow_up_questions"] == ["How is light captured?"]


# ---------------------------------------------------------------------------
# Structural xray check: shared chat subgraph is mounted once.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_xray_expands_chat_subgraph() -> None:
    """Flag-on graph exposes the shared chat subgraph to ``xray``.

    Structural check: ``StateGraph.get_graph(xray=True)`` walks the
    compiled graph and inlines any node whose body closes over a
    ``CompiledStateGraph``. Mounting chat via
    ``make_chat_node_wrapper(...)`` keeps the compiled subgraph at
    the wrapper's module-level globals, so ``find_subgraph_pregel``
    discovers it and the xray render carries node keys prefixed with
    ``chat:`` (the parent node name plus the subgraph node names).
    """
    agent = build_review_agent()
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert_subgraph_prefixes(node_keys, "chat:")


# ---------------------------------------------------------------------------
# Fan-out sites register Send triads.
# ---------------------------------------------------------------------------


def test_retrieve_send_triad_is_registered() -> None:
    """Graph registers the retrieve Send triad in place of ``retrieve_node``.

    The retrieve site fans out per dimension through
    ``retrieve_dispatch`` → ``retrieve_worker_node`` × N →
    ``retrieve_reduce_node``; no legacy single ``retrieve_node`` is
    registered.
    """
    agent = build_review_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "retrieve_dispatch" in node_keys
    assert "retrieve_worker_node" in node_keys
    assert "retrieve_reduce_node" in node_keys
    assert "retrieve_node" not in node_keys


def test_draft_send_triad_is_registered_flag_on() -> None:
    """Graph registers the draft Send triad in place of ``draft_node``.

    The draft site fans out per dimension through
    ``draft_dispatch`` → ``draft_worker_node`` × N →
    ``draft_reduce_node``; no legacy ``draft_node`` is registered.
    """
    agent = build_review_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "draft_dispatch" in node_keys
    assert "draft_worker_node" in node_keys
    assert "draft_reduce_node" in node_keys
    assert "draft_node" not in node_keys


def test_review_results_send_triad_is_registered_flag_on() -> None:
    """Flag-on graph registers the review_results Send triad.

    The review-results site fans out per dimension through
    ``review_results_dispatch`` → ``review_results_worker_node`` × N →
    ``review_results_reduce_node``; no legacy ``review_node`` is
    registered.
    """
    agent = build_review_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "review_results_dispatch" in node_keys
    assert "review_results_worker_node" in node_keys
    assert "review_results_reduce_node" in node_keys
    assert "review_node" not in node_keys


def test_revised_send_triad_is_registered_flag_on() -> None:
    """Flag-on graph registers the revised Send triad.

    The revised site fans out per dimension through
    ``revised_dispatch`` → ``revised_worker_node`` × N →
    ``revised_reduce_node``; no legacy ``revise_node`` is registered.
    """
    agent = build_review_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "revised_dispatch" in node_keys
    assert "revised_worker_node" in node_keys
    assert "revised_reduce_node" in node_keys
    assert "revise_node" not in node_keys
