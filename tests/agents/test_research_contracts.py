# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded in-silico research goal contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.research.contracts import (
    ResearchGoal,
    ResearchGoalBatch,
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


def test_research_goal_context_is_bounded() -> None:
    """Optional context is bounded before any remote submission."""
    with pytest.raises(ValidationError):
        ResearchGoal.model_validate(
            {"goal": "map drought genes", "context": "x" * 4001}
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
