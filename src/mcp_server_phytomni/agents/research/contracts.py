# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pydantic contracts for in-silico research goal extraction."""

from __future__ import annotations

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


class ResearchGoalBatch(RootModel[list[ResearchGoal]]):
    """Bounded non-empty list returned by the goal-extraction model."""

    root: list[ResearchGoal] = Field(min_length=1, max_length=20)

    def as_dicts(self) -> list[dict[str, str]]:
        """Return validated goals in the agent state shape."""
        return [
            {"goal": item.goal, "context": item.context or ""}
            for item in self.root
        ]

    @property
    def goal_count(self) -> int:
        """Return the number of validated goals in the batch."""
        return len(self.root)
