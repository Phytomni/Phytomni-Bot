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

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "RemoteSubmission",
    "SubmissionProtocolError",
    "WorkItemOutcome",
    "normalize_submission",
    "poll_work_item",
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


@dataclass(frozen=True)
class WorkItemOutcome:
    """Sanitized local outcome for one concrete remote work item.

    ``summary`` is populated only for a locally usable Markdown result.
    ``failure_reason`` contains fixed local text and never an upstream
    response body.  Intermediate ``pending`` and ``running`` transitions may
    be sent through the coordinator sink, but the value returned by
    :func:`poll_work_item` is always terminal.
    """

    status: str
    summary: str | None = None
    failure_reason: str | None = None

    @property
    def summary_markdown(self) -> str | None:
        """Expose the persistence-facing name used by later store code."""
        return self.summary


StatusReader = Callable[[str, float], Awaitable[Any] | Any]
ResultResolver = Callable[
    [RemoteSubmission], Awaitable[str | None] | str | None
]
TransitionSink = Callable[
    [str, str | None, str | None],
    Awaitable[WorkItemOutcome | None] | WorkItemOutcome | None,
]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[Any] | Any]


@dataclass(frozen=True)
class _PollOptions:
    """Runtime knobs for one coordinator polling loop."""

    request_timeout: float
    poll_interval: float
    deadline_seconds: float
    monotonic: Clock
    sleep: Sleep


_REMOTE_TO_LOCAL = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}
_REMOTE_FAILURE_REASONS = {
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
}
_UNKNOWN_STATUS_REASON = "analysis task returned an unknown status"
_STATUS_LOOKUP_REASON = "analysis status lookup failed"
_RESULT_RESOLUTION_REASON = "analysis result resolution failed"
_RESULT_UNUSABLE_REASON = "analysis result unusable"
_TIMEOUT_REASON = "analysis task timed out"
# Optional-job failures are caught only around one status/result callback;
# the polling loop itself has no blanket exception boundary.  CancelledError
# inherits BaseException and therefore remains outside this tuple.
_POLL_OPTIONAL_ERRORS: tuple[type[Exception], ...] = (Exception,)


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


def _remote_status(payload: Any) -> str | None:
    """Extract one normalized status without retaining the remote payload."""
    if isinstance(payload, Mapping):
        value = payload.get("status")
        if value is None:
            value = payload.get("task_status")
    else:
        value = payload
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized or None


async def _await_if_needed(value: Any) -> Any:
    """Await an injected callback result when it is awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _emit_transition(
    transition_sink: TransitionSink,
    status: str,
    summary: str | None,
    failure_reason: str | None,
) -> WorkItemOutcome:
    """Emit a transition and normalize sinks that return no value."""
    result = await _await_if_needed(
        transition_sink(status, summary, failure_reason)
    )
    if isinstance(result, WorkItemOutcome):
        return result
    return WorkItemOutcome(status, summary, failure_reason)


# pylint: disable=too-many-arguments
async def poll_work_item(
    submission: RemoteSubmission,
    *,
    status_reader: StatusReader,
    result_resolver: ResultResolver,
    transition_sink: TransitionSink,
    request_timeout: float,
    poll_interval: float,
    deadline_seconds: float,
    monotonic: Clock = time.monotonic,
    sleep: Sleep = asyncio.sleep,
) -> WorkItemOutcome:
    """Poll one remote job until a bounded local terminal outcome.

    The status reader receives the effective ``poll_task_id`` (which may be a
    deduplication source id), while the result resolver receives the complete
    normalized submission so it can download from the stable output path.
    ``monotonic`` and ``sleep`` are injected to make deadline behavior fully
    deterministic in unit tests.

    Ordinary status/resolution errors settle this optional work item with a
    fixed sanitized reason.  ``asyncio.CancelledError`` is intentionally not
    caught and therefore propagates to the parent coordinator unchanged.
    """
    poll_options = _PollOptions(
        request_timeout=float(request_timeout),
        poll_interval=float(poll_interval),
        deadline_seconds=float(deadline_seconds),
        monotonic=monotonic,
        sleep=sleep,
    )
    return await _poll_work_item(
        submission,
        status_reader,
        result_resolver,
        transition_sink,
        poll_options,
    )


# pylint: enable=too-many-arguments
async def _resolve_success(
    submission: RemoteSubmission,
    result_resolver: ResultResolver,
    transition_sink: TransitionSink,
) -> WorkItemOutcome:
    """Resolve a remote success into a usable local Markdown outcome."""
    try:
        resolved = await _await_if_needed(result_resolver(submission))
    except _POLL_OPTIONAL_ERRORS:
        return await _emit_transition(
            transition_sink,
            "failed",
            None,
            _RESULT_RESOLUTION_REASON,
        )
    if not isinstance(resolved, str):
        return await _emit_transition(
            transition_sink,
            "failed",
            None,
            _RESULT_UNUSABLE_REASON,
        )
    summary = resolved.strip()
    if not summary:
        return await _emit_transition(
            transition_sink,
            "failed",
            None,
            _RESULT_UNUSABLE_REASON,
        )
    return await _emit_transition(
        transition_sink,
        "succeeded",
        summary,
        None,
    )


async def _poll_work_item(
    submission: RemoteSubmission,
    status_reader: StatusReader,
    result_resolver: ResultResolver,
    transition_sink: TransitionSink,
    options: _PollOptions,
) -> WorkItemOutcome:
    """Run the bounded status loop with validated runtime options."""
    deadline = options.monotonic() + max(0.0, options.deadline_seconds)
    while True:
        if options.monotonic() >= deadline:
            return await _emit_transition(
                transition_sink,
                "timed_out",
                None,
                _TIMEOUT_REASON,
            )

        try:
            remote_payload = await _await_if_needed(
                status_reader(submission.poll_task_id, options.request_timeout)
            )
        except _POLL_OPTIONAL_ERRORS:
            return await _emit_transition(
                transition_sink,
                "failed",
                None,
                _STATUS_LOOKUP_REASON,
            )

        remote_status = _remote_status(remote_payload)
        local_status = _REMOTE_TO_LOCAL.get(remote_status or "")
        if local_status in {"pending", "running"}:
            await _emit_transition(
                transition_sink,
                local_status,
                None,
                None,
            )
            await _await_if_needed(options.sleep(options.poll_interval))
            continue

        if local_status == "succeeded":
            return await _resolve_success(
                submission,
                result_resolver,
                transition_sink,
            )

        if local_status in _REMOTE_FAILURE_REASONS:
            return await _emit_transition(
                transition_sink,
                local_status,
                None,
                _REMOTE_FAILURE_REASONS[local_status],
            )

        return await _emit_transition(
            transition_sink,
            "failed",
            None,
            _UNKNOWN_STATUS_REASON,
        )
