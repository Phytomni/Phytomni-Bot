# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Versioned public execution-event contract.

This module owns the finite, transport-neutral shape persisted by Bot and
decoded by Web.  It deliberately accepts only public metadata; diagnostic
objects, raw tool values, provider payloads, paths, and URLs belong elsewhere.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .execution_event_limits import DEFAULT_EXECUTION_EVENT_LIMITS
from .public_execution_safety import (
    PublicExecutionDataError,
    is_utc_timestamp,
    validate_public_execution_value,
)

PUBLIC_EVENT_KINDS = (
    "run.started",
    "run.accepted",
    "run.waiting_input",
    "run.resumed",
    "run.succeeded",
    "run.failed",
    "run.cancelled",
    "phase.started",
    "phase.progress",
    "phase.completed",
    "phase.failed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "todo.snapshot",
    "reasoning.summary",
    "decision.note",
    "input.required",
    "input.resolved",
    "artifact.published",
    "tracking.degraded",
)
PUBLIC_EVENT_KIND_SET = frozenset(PUBLIC_EVENT_KINDS)
PUBLIC_EVENT_STATUSES = frozenset(
    {"queued", "running", "waiting", "succeeded", "failed", "cancelled"}
)
PUBLIC_TODO_STATUSES = frozenset({"pending", "in_progress", "completed"})
PUBLIC_TARGET_KINDS = (
    "event",
    "artifact",
    "report",
    "todo",
    "preview",
    "download",
    "trace",
)
PUBLIC_TARGET_KIND_SET = frozenset(PUBLIC_TARGET_KINDS)
ExecutionEventStatus = Literal[
    "queued", "running", "waiting", "succeeded", "failed", "cancelled"
]
TerminalExecutionStatus = Literal["succeeded", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class ExecutionEventsCapabilityV1:
    """Discovery facts required before Web enables durable activity."""

    major_version: int = 1
    resumable_history: bool = True
    custom_event: str = "phyto.run_event"
    target_kinds: tuple[str, ...] = PUBLIC_TARGET_KINDS

    def to_public_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible discovery descriptor."""
        return {
            "major_version": self.major_version,
            "resumable_history": self.resumable_history,
            "custom_event": self.custom_event,
            "target_kinds": list(self.target_kinds),
        }


EXECUTION_EVENTS_CAPABILITY_V1 = ExecutionEventsCapabilityV1()

_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "run_id",
        "seq",
        "idempotency_key",
        "occurred_at",
        "kind",
        "status",
        "summary",
        "payload",
        "ignorable",
        "target",
        "task_id",
        "parent_event_id",
    }
)
_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "run.started": frozenset(),
    "run.accepted": frozenset(),
    "run.waiting_input": frozenset(),
    "run.resumed": frozenset(),
    "run.succeeded": frozenset(),
    "run.failed": frozenset({"code", "retryable"}),
    "run.cancelled": frozenset(),
    "phase.started": frozenset({"phase", "label_key"}),
    "phase.progress": frozenset({"phase", "completed", "total"}),
    "phase.completed": frozenset({"phase", "label_key"}),
    "phase.failed": frozenset({"phase", "code"}),
    "tool.started": frozenset({"tool_key", "call_id"}),
    "tool.completed": frozenset({"tool_key", "call_id", "duration_ms"}),
    "tool.failed": frozenset({"tool_key", "call_id", "duration_ms", "code"}),
    "todo.snapshot": frozenset({"items"}),
    "reasoning.summary": frozenset({"text"}),
    "decision.note": frozenset({"text"}),
    "input.required": frozenset({"surface_id", "widget"}),
    "input.resolved": frozenset({"surface_id", "outcome"}),
    "artifact.published": frozenset({"name", "media_type", "size_bytes"}),
    "tracking.degraded": frozenset({"code", "retryable"}),
}


class ExecutionEventValidationError(ValueError):
    """A public event or projection failed the finite V1 contract."""


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicEventSummary(_PublicModel):
    """Bounded localization identity plus safe text fallback."""

    key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=512)


class PublicResourceTarget(_PublicModel):
    """Opaque resource reference resolved under current ownership."""

    kind: Literal["event", "artifact", "report", "todo", "preview", "download"]
    id: str = Field(min_length=1, max_length=256)


class PublicTodoItem(_PublicModel):
    """One item in an atomic Todo snapshot."""

    id: str = Field(min_length=1, max_length=128)
    label_key: str = Field(min_length=1, max_length=128)
    status: Literal["pending", "in_progress", "completed"]


class PublicEmptyPayload(_PublicModel):
    """Payload for lifecycle events with no additional public fields."""


class PublicRunFailurePayload(_PublicModel):
    """Stable failure classification without an exception message."""

    code: str = Field(min_length=1, max_length=128)
    retryable: bool


class PublicPhasePayload(_PublicModel):
    """Stable semantic phase identity."""

    phase: str = Field(min_length=1, max_length=128)
    label_key: str = Field(min_length=1, max_length=128)


class PublicPhaseProgressPayload(_PublicModel):
    """Optional exact progress counters for one semantic phase."""

    phase: str = Field(min_length=1, max_length=128)
    completed: int = Field(ge=0)
    total: int = Field(ge=1)


class PublicPhaseFailurePayload(_PublicModel):
    """Bounded phase failure classification."""

    phase: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=128)


class PublicToolStartedPayload(_PublicModel):
    """Safe shared tool identity without arguments."""

    tool_key: str = Field(min_length=1, max_length=128)
    call_id: str = Field(min_length=1, max_length=128)


class PublicToolCompletedPayload(PublicToolStartedPayload):
    """Safe tool completion metadata."""

    duration_ms: int = Field(ge=0)


class PublicToolFailedPayload(PublicToolCompletedPayload):
    """Safe tool failure classification."""

    code: str = Field(min_length=1, max_length=128)


class PublicTodoSnapshotPayload(_PublicModel):
    """Whole-list Todo replacement."""

    items: tuple[PublicTodoItem, ...]


class PublicTextPayload(_PublicModel):
    """Bounded public summary or explicit decision text."""

    text: str = Field(min_length=1, max_length=512)


class PublicInputRequiredPayload(_PublicModel):
    """Safe identity of an interactive surface awaiting input."""

    surface_id: str = Field(min_length=1, max_length=128)
    widget: str = Field(min_length=1, max_length=64)


class PublicInputResolvedPayload(_PublicModel):
    """Safe outcome of one interactive surface."""

    surface_id: str = Field(min_length=1, max_length=128)
    outcome: str = Field(min_length=1, max_length=64)


class PublicArtifactPayload(_PublicModel):
    """Bounded artifact metadata; content remains in artifact storage."""

    name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)


class PublicTrackingDegradedPayload(_PublicModel):
    """Finite tracking degradation without private storage errors."""

    code: str = Field(min_length=1, max_length=128)
    retryable: bool


class PublicExtensionPayload(BaseModel):
    """Safe data carried only by an explicitly ignorable extension."""

    model_config = ConfigDict(extra="allow", frozen=True)


PublicEventPayload = (
    PublicEmptyPayload
    | PublicRunFailurePayload
    | PublicPhasePayload
    | PublicPhaseProgressPayload
    | PublicPhaseFailurePayload
    | PublicToolStartedPayload
    | PublicToolCompletedPayload
    | PublicToolFailedPayload
    | PublicTodoSnapshotPayload
    | PublicTextPayload
    | PublicInputRequiredPayload
    | PublicInputResolvedPayload
    | PublicArtifactPayload
    | PublicTrackingDegradedPayload
    | PublicExtensionPayload
)

_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "run.started": PublicEmptyPayload,
    "run.accepted": PublicEmptyPayload,
    "run.waiting_input": PublicEmptyPayload,
    "run.resumed": PublicEmptyPayload,
    "run.succeeded": PublicEmptyPayload,
    "run.failed": PublicRunFailurePayload,
    "run.cancelled": PublicEmptyPayload,
    "phase.started": PublicPhasePayload,
    "phase.progress": PublicPhaseProgressPayload,
    "phase.completed": PublicPhasePayload,
    "phase.failed": PublicPhaseFailurePayload,
    "tool.started": PublicToolStartedPayload,
    "tool.completed": PublicToolCompletedPayload,
    "tool.failed": PublicToolFailedPayload,
    "todo.snapshot": PublicTodoSnapshotPayload,
    "reasoning.summary": PublicTextPayload,
    "decision.note": PublicTextPayload,
    "input.required": PublicInputRequiredPayload,
    "input.resolved": PublicInputResolvedPayload,
    "artifact.published": PublicArtifactPayload,
    "tracking.degraded": PublicTrackingDegradedPayload,
}


class PublicResultItem(_PublicModel):
    """One bounded result projection backed by a typed target."""

    event_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    target: PublicResourceTarget


class PublicTerminalState(_PublicModel):
    """Terminal outcome cached in the replaceable projection."""

    status: TerminalExecutionStatus
    event_id: str = Field(min_length=1, max_length=128)


class ExecutionEventV1(_PublicModel):
    """Immutable public event envelope for one Bot run."""

    schema_version: Literal[1]
    event_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=1)
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    occurred_at: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=128)
    status: ExecutionEventStatus
    summary: PublicEventSummary
    payload: PublicEventPayload
    ignorable: bool = False
    target: PublicResourceTarget | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_event_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )

    @property
    def is_known(self) -> bool:
        """Whether this event participates in the V1 public projection."""
        return self.kind in PUBLIC_EVENT_KIND_SET

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize only declared public fields."""
        return self.model_dump(mode="json", exclude_none=True)


class ExecutionEventIntent(_PublicModel):
    """Transport-neutral producer intent before store sequence allocation."""

    kind: str = Field(min_length=1, max_length=128)
    status: ExecutionEventStatus
    summary: PublicEventSummary
    payload: PublicEventPayload
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    target: PublicResourceTarget | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_event_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )

    def materialize(
        self,
        *,
        run_id: str,
        seq: int,
        event_id: str,
        occurred_at: str,
    ) -> ExecutionEventV1:
        """Attach store-owned identity and ordering to this intent."""
        raw = self.model_dump(mode="json", exclude_none=True)
        raw.update(
            {
                "schema_version": 1,
                "run_id": run_id,
                "seq": seq,
                "event_id": event_id,
                "occurred_at": occurred_at,
                "ignorable": False,
            }
        )
        return parse_execution_event(raw)


class RunEventProjectionV1(_PublicModel):
    """Replaceable summary cache derivable from ordered events."""

    schema_version: Literal[1]
    run_id: str = Field(min_length=1, max_length=128)
    latest_seq: int = Field(ge=0)
    status: ExecutionEventStatus
    phase: str | None = Field(default=None, max_length=128)
    todos: tuple[PublicTodoItem, ...] = ()
    results: tuple[PublicResultItem, ...] = ()
    input_required: PublicInputRequiredPayload | None = None
    terminal: PublicTerminalState | None = None

    def to_public_dict(self) -> dict[str, Any]:
        """Return the stable JSON-compatible cache representation."""
        return self.model_dump(mode="json")


def _fail(reason: str) -> NoReturn:
    raise ExecutionEventValidationError(reason)


def _validate_utc(value: object) -> None:
    if not is_utc_timestamp(value):
        _fail("invalid_occurred_at")


def _validate_public_value(value: object) -> None:
    try:
        validate_public_execution_value(
            value,
            max_string_chars=DEFAULT_EXECUTION_EVENT_LIMITS.max_summary_chars,
        )
    except PublicExecutionDataError as exc:
        raise ExecutionEventValidationError(str(exc)) from exc


def _validate_todos(payload: Mapping[str, object]) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        _fail("invalid_todo_snapshot")
    try:
        DEFAULT_EXECUTION_EVENT_LIMITS.validate_todo_count(len(items))
    except ValueError as exc:
        raise ExecutionEventValidationError(str(exc)) from exc
    try:
        tuple(PublicTodoItem.model_validate(item) for item in items)
    except ValidationError as exc:
        raise ExecutionEventValidationError("invalid_todo_snapshot") from exc


def _validate_payload(kind: str, payload: object, *, known: bool) -> None:
    if not isinstance(payload, Mapping):
        _fail("invalid_public_payload")
    _validate_public_value(payload)
    if not known:
        return
    allowed = _PAYLOAD_FIELDS[kind]
    if not set(payload).issubset(allowed):
        _fail("invalid_public_payload")
    if kind == "todo.snapshot":
        _validate_todos(payload)


def _typed_payload(
    kind: str, payload: object, *, known: bool
) -> PublicEventPayload:
    model: type[BaseModel] | None = (
        _PAYLOAD_MODELS.get(kind) if known else PublicExtensionPayload
    )
    if model is None:
        _fail("invalid_public_payload")
    try:
        return cast(PublicEventPayload, model.model_validate(payload))
    except ValidationError as exc:
        reason = (
            "invalid_todo_snapshot"
            if kind == "todo.snapshot"
            else "invalid_public_payload"
        )
        raise ExecutionEventValidationError(reason) from exc


def parse_execution_event(value: object) -> ExecutionEventV1:
    """Decode an event, rejecting unknown required or unsafe content."""
    if not isinstance(value, Mapping):
        _fail("invalid_event")
    raw = dict(value)
    if not set(raw).issubset(_EVENT_FIELDS):
        _fail("invalid_event")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        _fail("invalid_event_kind")
    known = kind in PUBLIC_EVENT_KIND_SET
    if not known and raw.get("ignorable") is not True:
        _fail("unknown_required_kind")
    target = raw.get("target")
    if (
        isinstance(target, Mapping)
        and target.get("kind") not in PUBLIC_TARGET_KIND_SET
    ):
        _fail("unknown_target_kind")
    _validate_public_value(raw.get("summary"))
    _validate_payload(kind, raw.get("payload"), known=known)
    raw["payload"] = _typed_payload(kind, raw.get("payload"), known=known)
    _validate_utc(raw.get("occurred_at"))
    try:
        event = ExecutionEventV1.model_validate(raw)
    except ValidationError as exc:
        if "target" in str(exc):
            raise ExecutionEventValidationError("unknown_target_kind") from exc
        raise ExecutionEventValidationError("invalid_event") from exc
    try:
        DEFAULT_EXECUTION_EVENT_LIMITS.validate_summary(event.summary.text)
        encoded = json.dumps(event.to_public_dict(), ensure_ascii=False)
        DEFAULT_EXECUTION_EVENT_LIMITS.validate_event_size(
            len(encoded.encode("utf-8"))
        )
    except ValueError as exc:
        raise ExecutionEventValidationError(str(exc)) from exc
    return event


def parse_execution_event_intent(value: object) -> ExecutionEventIntent:
    """Decode one finite producer intent through the public safety contract."""
    if not isinstance(value, Mapping):
        _fail("invalid_event_intent")
    raw = dict(value)
    allowed = {
        "kind",
        "status",
        "summary",
        "payload",
        "idempotency_key",
        "target",
        "task_id",
        "parent_event_id",
    }
    if not set(raw).issubset(allowed):
        _fail("invalid_event_intent")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        _fail("invalid_event_kind")
    if kind not in PUBLIC_EVENT_KIND_SET:
        _fail("unknown_required_kind")
    target = raw.get("target")
    if (
        isinstance(target, Mapping)
        and target.get("kind") not in PUBLIC_TARGET_KIND_SET
    ):
        _fail("unknown_target_kind")
    _validate_public_value(raw.get("summary"))
    _validate_payload(kind, raw.get("payload"), known=True)
    raw["payload"] = _typed_payload(kind, raw.get("payload"), known=True)
    try:
        return ExecutionEventIntent.model_validate(raw)
    except ValidationError as exc:
        raise ExecutionEventValidationError("invalid_event_intent") from exc


def parse_run_event_projection(value: object) -> RunEventProjectionV1:
    """Decode a bounded, replaceable V1 run projection."""
    _validate_public_value(value)
    try:
        return RunEventProjectionV1.model_validate(value)
    except ValidationError as exc:
        raise ExecutionEventValidationError("invalid_projection") from exc
