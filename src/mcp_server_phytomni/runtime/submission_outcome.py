# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed outcomes for remote Analyst submissions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "AcceptedSubmission",
    "RejectedSubmission",
    "SubmissionOutcome",
    "classify_submissions",
    "has_pending_a2a",
    "rejected_submissions_from_state",
    "project_submission_warnings",
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


def rejected_submissions_from_state(
    result: Mapping[str, object],
    *,
    pending_keys: tuple[str, ...],
) -> list[RejectedSubmission]:
    """Project safe rejection records from a graph result state."""
    state = result.get("phytomni_state")
    if not isinstance(state, Mapping):
        return []
    rejected: list[RejectedSubmission] = []
    raw_rejections = state.get("submission_rejections")
    if isinstance(raw_rejections, list):
        for item in raw_rejections:
            if not isinstance(item, Mapping):
                continue
            goal = item.get("goal")
            code = item.get("code")
            if isinstance(goal, str) and isinstance(code, str):
                rejected.append(RejectedSubmission(goal=goal, code=code))
    raw_pending = state.get("a2a_pending")
    if isinstance(raw_pending, list):
        for item in raw_pending:
            if not isinstance(item, Mapping):
                continue
            goal = next(
                (
                    value
                    for key in pending_keys
                    if isinstance(value := item.get(key), str)
                    and value.strip()
                ),
                "external_a2a",
            )
            rejected.append(
                RejectedSubmission(goal=goal, code="a2a_input_required")
            )
    return rejected


def has_pending_a2a(result: Mapping[str, object]) -> bool:
    """Return whether a graph result retains an unresolved A2A pause."""
    state = result.get("phytomni_state")
    pending = state.get("a2a_pending") if isinstance(state, Mapping) else None
    return isinstance(pending, list) and bool(pending)


def project_submission_warnings(raw: object) -> list[dict[str, object]]:
    """Keep only safe warning fields for durable lifecycle payloads."""
    if not isinstance(raw, list):
        return []
    projected: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        warning: dict[str, object] = {"code": code}
        retryable = item.get("retryable")
        if isinstance(retryable, bool):
            warning["retryable"] = retryable
        rejected_count = item.get("rejected_count")
        if isinstance(rejected_count, int) and rejected_count >= 0:
            warning["rejected_count"] = rejected_count
        projected.append(warning)
    return projected
