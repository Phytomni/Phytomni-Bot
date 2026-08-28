# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded in-silico research goal contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.research.contracts import (
    MAX_RESEARCH_GOAL_CHARS,
    MAX_RESEARCH_GOAL_CONTEXT_CHARS,
    ResearchGoal,
    ResearchGoalBatch,
)
from mcp_server_phytomni.agents.research.goal_extraction import (
    MAX_GOAL_EVIDENCE_CHARS,
)

pytestmark = pytest.mark.agent


def test_research_goal_batch_rejects_empty_and_blank() -> None:
    """Goal extraction must contain at least one nonblank objective."""
    with pytest.raises(ValidationError):
        ResearchGoalBatch.model_validate([])
    with pytest.raises(ValidationError):
        ResearchGoalBatch.model_validate(
            [{"goal": "   ", "context": "bounded"}]
        )


def test_research_goal_text_accepts_the_evidence_aligned_budget() -> None:
    """Per-item goal+context may fill the 131072 evidence character budget."""
    assert (
        MAX_RESEARCH_GOAL_CHARS + MAX_RESEARCH_GOAL_CONTEXT_CHARS
        == MAX_GOAL_EVIDENCE_CHARS
    )
    goal = ResearchGoal.model_validate(
        {
            "goal": "g" * MAX_RESEARCH_GOAL_CHARS,
            "context": "c" * MAX_RESEARCH_GOAL_CONTEXT_CHARS,
        }
    )
    assert len(goal.goal) == MAX_RESEARCH_GOAL_CHARS
    assert goal.context is not None
    assert len(goal.context) == MAX_RESEARCH_GOAL_CONTEXT_CHARS


def test_research_goal_context_is_bounded() -> None:
    """Optional context is bounded before any remote submission."""
    with pytest.raises(ValidationError):
        ResearchGoal.model_validate(
            {
                "goal": "map drought genes",
                "context": "x" * (MAX_RESEARCH_GOAL_CONTEXT_CHARS + 1),
            }
        )
    with pytest.raises(ValidationError):
        ResearchGoal.model_validate(
            {"goal": "g" * (MAX_RESEARCH_GOAL_CHARS + 1)}
        )


def test_research_goal_normalizes_text_and_forbids_extra_fields() -> None:
    """The model strips accepted text and rejects provider drift."""
    goal = ResearchGoal.model_validate(
        {"goal": "  map drought genes  ", "context": " bounded "}
    )
    assert goal.goal == "map drought genes"
    assert goal.context == "bounded"

    with pytest.raises(ValidationError):
        ResearchGoal.model_validate(
            {"goal": "map drought genes", "unexpected": True}
        )
