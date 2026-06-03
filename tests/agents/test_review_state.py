# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for DeepResearchState's inheritance + new field schema."""

from typing import get_type_hints

import pytest

from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.agents.shared.parallel_dispatch import (
    ParallelDispatchState,
)

pytestmark = pytest.mark.agent


def test_deep_research_state_inherits_parallel_dispatch_state() -> None:
    """DeepResearchState MUST inherit ParallelDispatchState so review
    participates in the universal failures channel.

    TypedDict uses structural inheritance: fields from the parent merge
    into the child's combined type hints at class-definition time.
    Verify that all six ParallelDispatchState fields appear in
    DeepResearchState's get_type_hints() result.
    """
    parent_fields = set(get_type_hints(ParallelDispatchState))
    child_fields = set(get_type_hints(DeepResearchState, include_extras=True))
    missing = parent_fields - child_fields
    assert not missing, f"ParallelDispatchState fields missing: {missing}"


def test_review_state_carries_chat_mount_fields() -> None:
    """Single-shot chat-mount pattern fields (Step 6.0 lock)."""
    hints = get_type_hints(DeepResearchState, include_extras=True)
    for key in ["chat_payload", "chat_response", "pending_post"]:
        assert key in hints, f"chat-mount field {key} missing"


def test_review_state_carries_send_transient_fields() -> None:
    """Send-payload transient fields declared on state."""
    hints = get_type_hints(DeepResearchState, include_extras=True)
    for key in [
        "subtopic",
        "knowledge",
        "dimension",
        "review_draft",
        "original_draft",
        "review_feedback",
        "knowledge_payload",
    ]:
        assert key in hints, f"transient field {key} missing"


def test_review_state_carries_indexed_accumulators() -> None:
    """4 fan-out sites each have their indexed_results accumulator."""
    hints = get_type_hints(DeepResearchState, include_extras=True)
    for key in [
        "retrieve_indexed_results",
        "draft_indexed_results",
        "review_indexed_results",
        "revised_indexed_results",
    ]:
        assert key in hints, f"accumulator {key} missing"


def test_review_state_carries_final_ordered_outputs() -> None:
    """Final ordered outputs written by reduce_node."""
    hints = get_type_hints(DeepResearchState, include_extras=True)
    for key in [
        "draft_contents",
        "review_contents",
        "revised_contents",
    ]:
        assert key in hints, f"final output {key} missing"


def test_review_state_inherits_failures_channel() -> None:
    """failures channel from ParallelDispatchState is present."""
    hints = get_type_hints(DeepResearchState, include_extras=True)
    assert "failures" in hints, "failures not inherited from parent"
