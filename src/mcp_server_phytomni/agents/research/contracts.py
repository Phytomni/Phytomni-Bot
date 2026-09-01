# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pydantic contracts for in-silico research goal extraction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

# Per-item budgets sum to MAX_GOAL_EVIDENCE_CHARS (131072).
MAX_RESEARCH_GOAL_CHARS = 16_384
MAX_RESEARCH_GOAL_CONTEXT_CHARS = 114_688

__all__ = [
    "MAX_RESEARCH_GOAL_CHARS",
    "MAX_RESEARCH_GOAL_CONTEXT_CHARS",
    "ResearchGoal",
    "ResearchGoalBatch",
]


class ResearchGoal(BaseModel):
    """One bounded, nonblank research objective."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=MAX_RESEARCH_GOAL_CHARS)
    context: str | None = Field(
        default=None, max_length=MAX_RESEARCH_GOAL_CONTEXT_CHARS
    )
    dataset_ids: tuple[str, ...] | None = Field(default=None)

    @field_validator("goal", "context")
    @classmethod
    def strip_nonblank(cls, value: str | None) -> str | None:
        """Normalize text and reject whitespace-only values."""
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("value must be nonblank")
        return clean

    @field_validator("dataset_ids", mode="before")
    @classmethod
    def coerce_dataset_ids(cls, value: object) -> tuple[str, ...] | None:
        """Coerce cited ids; unusable values fail-open to unbound."""
        if value is None or not isinstance(value, (list, tuple)):
            return None
        seen: set[str] = set()
        ordered: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            clean = item.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        return tuple(ordered)

    def as_state(self) -> dict[str, Any]:
        """Project one goal into graph/HTTP child-task state."""
        payload: dict[str, Any] = {
            "goal": self.goal,
            "context": self.context or "",
        }
        if self.dataset_ids is not None:
            payload["dataset_ids"] = list(self.dataset_ids)
        return payload


class ResearchGoalBatch(RootModel[list[ResearchGoal]]):
    """Bounded non-empty list returned by the goal-extraction model."""

    root: list[ResearchGoal] = Field(min_length=1, max_length=20)

    def as_dicts(self) -> list[dict[str, Any]]:
        """Return validated goals in the agent state shape."""
        return [item.as_state() for item in self.root]

    @property
    def goal_count(self) -> int:
        """Return the number of validated goals in the batch."""
        return len(self.root)
