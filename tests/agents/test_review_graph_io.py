# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO contract tests for DeepResearchAgent (review) subgraph.

Pins the public ``DeepResearchInput`` / ``DeepResearchOutput`` shape
and the full ``DeepResearchState`` legacy field set. Reflective for
the schema parts.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from mcp_server_phytomni.agents.review import DeepResearchState
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import (
    DeepResearchInput,
    DeepResearchOutput,
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
    ``DeepResearchState`` for its ``state`` parameter, and the legacy
    field union sourced from state.py is fully preserved. New fields
    (e.g. universal failure channel inherited from
    ``ParallelDispatchState``, Send-payload transient fields, indexed
    accumulators added for the fan-out wire) are additive — this test
    enforces the superset relation so legacy callers never lose a
    field while new fields can land without re-pinning the set.
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
    assert set(get_type_hints(DeepResearchState).keys()) >= expected


def test_review_initial_state_seeds_interop_channels() -> None:
    """Seed inherited interop reducers before the graph starts."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    state = agent.initial_state("plant stress review")

    assert not state["interop"]
    assert state["degraded_interop"] is False


def test_review_initial_state_seeds_private_conversation_metadata() -> None:
    """Review state carries bounded operation metadata without public IO changes."""
    agent = DeepResearchAgent.__new__(DeepResearchAgent)
    state = agent.initial_state("plant stress review")

    assert state["review_operation"] is None
    assert state["report_artifact_id"] is None
    assert state["report_revision"] == 0
