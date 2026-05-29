# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO contract tests for DeepResearchAgent (review) subgraph.

Pins the public ``DeepResearchInput`` / ``DeepResearchOutput``
shape and confirms the compiled subgraph carries no conditional
branches (review is a linear seven-node pipeline). Reflective for
the schema parts; smoke for the topology.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from mcp_server_phytomni.agents.review import (
    DeepResearchAgent,
    DeepResearchInput,
    DeepResearchOutput,
    DeepResearchState,
)

pytestmark = pytest.mark.agent


def _required_keys(td: type) -> set[str]:
    return set(getattr(td, "__required_keys__", set()))


def _optional_keys(td: type) -> set[str]:
    return set(getattr(td, "__optional_keys__", set()))


def test_review_input_requires_only_original_user_query() -> None:
    """``DeepResearchInput`` requires exactly ``original_user_query``.

    Parent graphs owe the subgraph one field (the legacy state
    seed). ``obs_file_list`` is optional so a caller with no
    uploaded files supplies a single key and the graph runs.
    """
    assert _required_keys(DeepResearchInput) == {"original_user_query"}
    assert "obs_file_list" in _optional_keys(DeepResearchInput)


def test_review_output_exposes_summary_and_final_response() -> None:
    """``DeepResearchOutput`` carries the chat answer + raw summary text.

    Parent graphs reading the review result see the
    chat-completions-style ``final_response`` envelope and the raw
    ``summary_content`` markdown. Internal scratch (per-dimension
    drafts, reviews, revisions) stays hidden.
    """
    hints = get_type_hints(DeepResearchOutput)
    assert set(hints.keys()) == {"final_response", "summary_content"}


def test_review_state_carries_full_legacy_field_set() -> None:
    """``DeepResearchState`` retains every legacy inline TypedDict key.

    Pins binary compatibility: every node method still type-hints
    ``DeepResearchState`` for its ``state`` parameter, and that
    name is now sourced from state.py with the identical field
    union.
    """
    expected = {
        "original_user_query",
        "user_query",
        "obs_file_list",
        "upload_context",
        "total_length",
        "research_dimensions",
        "all_raw_doc_list",
        "dimension_params",
        "draft_contents",
        "review_contents",
        "revised_reports",
        "add_doc_list",
        "summary_content",
        "final_response",
    }
    assert set(get_type_hints(DeepResearchState).keys()) == expected


def test_review_subgraph_compiles_with_no_conditional_branches() -> None:
    """DeepResearchAgent compiles into a linear seven-node pipeline.

    Pins the absence of conditional routing: plan → retrieve →
    draft → review → revise → summary → post_process is a single
    edge chain. A future refactor adding branches surfaces here
    before downstream consumers notice.
    """
    agent = DeepResearchAgent()
    assert set(agent.app.builder.branches.keys()) == set()
    nodes = {n.id for n in agent.app.get_graph().nodes.values()}
    assert {
        "plan_node",
        "retrieve_node",
        "draft_node",
        "review_node",
        "revise_node",
        "summary_node",
        "post_process_node",
    } <= nodes
