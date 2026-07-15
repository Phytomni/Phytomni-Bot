# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contracts for normalizing remote DeepGenome submissions.

The analysis platform returns the caller-owned task id and, on a
deduplication hit, a second id for the existing remote task.  Keep those
identities explicit so the local task registry can address the caller's row
while polling the effective remote job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "RemoteSubmission",
    "SubmissionProtocolError",
    "normalize_submission",
]


class SubmissionProtocolError(ValueError):
    """Raised when an analysis submission acknowledgement is malformed."""


@dataclass(frozen=True)
class RemoteSubmission:
    """Normalized caller and effective remote identities.

    Attributes:
        submitted_task_id: Task id owned by the caller that submitted the
            request.  This remains distinct from any reused remote id.
        poll_task_id: Remote task id that should be used for status polling.
        output_dir: Output directory returned by the analysis platform.
    """

    submitted_task_id: str
    poll_task_id: str
    output_dir: str


def _nonblank(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` for a blank/non-string value."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_submission(
    payload: Mapping[str, Any],
) -> RemoteSubmission:
    """Normalize and validate one analysis submission acknowledgement.

    ``task_id`` and ``output_dir`` are required and must be nonblank strings.
    A nonblank ``source_task_id`` identifies a reused remote task and becomes
    the effective polling id; otherwise the caller-owned id is polled.
    Failure text is deliberately fixed so an upstream payload is never
    copied into an exception message or log by this protocol boundary.

    Args:
        payload: Mapping returned by the analysis submission call.

    Returns:
        Frozen normalized submission identities.

    Raises:
        SubmissionProtocolError: If the required fields are missing or blank.
    """
    if not isinstance(payload, Mapping):
        raise SubmissionProtocolError("invalid analysis submission")
    submitted = _nonblank(payload.get("task_id"))
    output_dir = _nonblank(payload.get("output_dir"))
    if submitted is None or output_dir is None:
        raise SubmissionProtocolError("invalid analysis submission")
    poll_id = _nonblank(payload.get("source_task_id")) or submitted
    return RemoteSubmission(submitted, poll_id, output_dir)
