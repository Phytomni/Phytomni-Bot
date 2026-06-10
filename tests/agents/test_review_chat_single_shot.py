# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ReviewAgent's three single-shot chat sites.

The three single-shot chat sites (``plan_query`` / ``summary`` /
``follow_up``) route through prep + post pairs surrounding a single
shared chat node registered via ``add_node`` from the
``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for the single-shot chat sites."""
    config = ReviewConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": False,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Prep nodes stage ``chat_payload`` + ``pending_post``.
# ---------------------------------------------------------------------------


async def test_plan_query_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for plan_query."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "How does photosynthesis work?",
            "obs_file_list": [],
        },
    )
    result = await agent.plan_query_prep_node(state)

    assert result["pending_post"] == "plan_query_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "How does photosynthesis work?" in chat_payload["user_query"]
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_summary_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for summary."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "revised_reports": [
                {"subtopic": "dim1", "revised_report": "Content 1"},
                {"subtopic": "dim2", "revised_report": "Content 2"},
            ],
            "research_dimensions": ["dim1", "dim2"],
        },
    )
    result = await agent.summary_prep_node(state)

    assert result["pending_post"] == "summary_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "Photosynthesis" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_follow_up_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for follow_up."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "A review of photosynthesis.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
        },
    )
    result = await agent.follow_up_prep_node(state)

    assert result["pending_post"] == "follow_up_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "Photosynthesis" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["with_follow_up"] is True


# ---------------------------------------------------------------------------
# Flag-on post nodes parse ``state['chat_response']`` into the legacy delta.
# ---------------------------------------------------------------------------


async def test_plan_query_post_node_parses_chat_response() -> None:
    """Post node parses ``chat_response`` into the plan_query delta."""
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)


# ---------------------------------------------------------------------------
# Fan-out sites retain legacy gather bodies under flag-on.
# ---------------------------------------------------------------------------


def test_retrieve_node_is_registered_flag_on() -> None:
    """Flag-on graph registers ``retrieve_node`` (legacy gather body)."""
    agent = _build_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "retrieve_node" in node_keys


def test_draft_send_triad_is_registered_flag_on() -> None:
    """Graph registers the draft Send triad in place of ``draft_node``.

    The draft site fans out per dimension through
    ``draft_dispatch`` → ``draft_worker_node`` × N →
    ``draft_reduce_node``; no legacy ``draft_node`` is registered.
    """
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
    node_keys = set(agent.app.get_graph().nodes.keys())
    assert "revised_dispatch" in node_keys
    assert "revised_worker_node" in node_keys
    assert "revised_reduce_node" in node_keys
    assert "revise_node" not in node_keys
