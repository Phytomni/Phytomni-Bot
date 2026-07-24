# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pydantic contracts for in-silico research goal extraction."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

__all__ = ["ResearchGoal", "ResearchGoalBatch"]


class ResearchGoal(BaseModel):
    """One bounded, nonblank research objective."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=1000)
    context: str | None = Field(default=None, max_length=4000)

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


class ResearchGoalBatch(RootModel[list[ResearchGoal]]):
    """Bounded non-empty list returned by the goal-extraction model."""

    root: list[ResearchGoal] = Field(min_length=1, max_length=20)
