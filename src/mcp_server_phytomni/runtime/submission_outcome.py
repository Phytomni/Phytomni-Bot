# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed outcomes for remote Analyst submissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "AcceptedSubmission",
    "RejectedSubmission",
    "SubmissionOutcome",
    "classify_submissions",
]


@dataclass(frozen=True, slots=True)
class AcceptedSubmission:
    """One remote submission accepted with a usable task identity."""

    task_id: str
    output_dir: str

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("accepted task id must be nonblank")


@dataclass(frozen=True, slots=True)
class RejectedSubmission:
    """One remote submission rejected before a usable task identity."""

    goal: str
    code: str


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """Aggregate classification for one or more remote submissions."""

    kind: Literal["full", "partial", "rejected"]
    accepted: tuple[AcceptedSubmission, ...]
    rejected: tuple[RejectedSubmission, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        """Return only task IDs belonging to accepted submissions."""
        return tuple(item.task_id for item in self.accepted)

    @property
    def warnings(self) -> tuple[dict[str, object], ...]:
        """Return structured warning metadata for a partial outcome."""
        if self.kind != "partial":
            return ()
        return (
            {
                "code": "partial_submission",
                "retryable": False,
                "rejected_count": len(self.rejected),
            },
        )


def classify_submissions(
    *,
    accepted: list[AcceptedSubmission],
    rejected: list[RejectedSubmission],
) -> SubmissionOutcome:
    """Classify a batch of accepted and rejected remote submissions."""
    kind: Literal["full", "partial", "rejected"]
    if accepted and rejected:
        kind = "partial"
    elif accepted:
        kind = "full"
    else:
        kind = "rejected"
    return SubmissionOutcome(kind, tuple(accepted), tuple(rejected))
