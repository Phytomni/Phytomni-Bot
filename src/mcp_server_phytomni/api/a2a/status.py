# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Project internal Phytomni run statuses onto A2A v1 task states."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import Message, Part, Role, TaskState, TaskStatus

__all__ = [
    "A2ATaskStateProjection",
    "build_task_status",
    "project_task_state",
]

_SUBMITTED = frozenset({"pending", "queued", "submitted"})
_WORKING = frozenset({"in-progress", "in_progress", "running", "working"})
_COMPLETED = frozenset({"completed", "done", "success", "succeeded"})
_FAILED = frozenset({"error", "failed", "failure"})
_CANCELED = frozenset({"cancel", "cancelled", "canceled"})
_INPUT_REQUIRED = frozenset({"input-required", "input_required"})
_AUTH_REQUIRED = frozenset({"auth-required", "auth_required"})
_REJECTED = frozenset({"reject", "rejected"})
_STATUS_STATES = {
    **dict.fromkeys(_SUBMITTED, TaskState.TASK_STATE_SUBMITTED),
    **dict.fromkeys(_WORKING, TaskState.TASK_STATE_WORKING),
    **dict.fromkeys(_COMPLETED, TaskState.TASK_STATE_COMPLETED),
    **dict.fromkeys(_FAILED, TaskState.TASK_STATE_FAILED),
    **dict.fromkeys(_CANCELED, TaskState.TASK_STATE_CANCELED),
    **dict.fromkeys(_INPUT_REQUIRED, TaskState.TASK_STATE_INPUT_REQUIRED),
    **dict.fromkeys(_AUTH_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED),
    **dict.fromkeys(_REJECTED, TaskState.TASK_STATE_REJECTED),
}


@dataclass(frozen=True)
class A2ATaskStateProjection:
    """A mapped A2A state plus an optional audit detail."""

    task_state: int
    detail: str | None = None


def _normalized_status(status: str) -> str:
    """Normalize only separators and case; preserve unknown values."""
    return status.strip().lower().replace(" ", "-")


def project_task_state(status: str) -> A2ATaskStateProjection:
    """Map an internal status string to an exhaustive A2A v1 state.

    Unknown values deliberately become ``TASK_STATE_FAILED`` instead of
    being guessed as an in-flight or successful task. The original value is
    retained in ``detail`` for structured audit logging by the caller.
    """
    normalized = _normalized_status(status)
    mapped_state = _STATUS_STATES.get(normalized)
    if mapped_state is not None:
        return A2ATaskStateProjection(mapped_state)
    return A2ATaskStateProjection(
        TaskState.TASK_STATE_FAILED,
        detail=f"unmapped internal run status: {status!r}",
    )


def build_task_status(
    status: str,
    *,
    phase: str | None = None,
    detail: str | None = None,
) -> TaskStatus:
    """Build an A2A ``TaskStatus`` without conflating phase and state."""
    projection = project_task_state(status)
    message_parts = [item for item in (projection.detail, detail) if item]
    if phase:
        message_parts.append(f"phase: {phase}")
    message = None
    if message_parts:
        message = Message(
            role=Role.ROLE_AGENT,
            parts=[Part(text="; ".join(message_parts))],
        )
    return TaskStatus(
        state=TaskState.Name(projection.task_state),
        message=message,
    )
