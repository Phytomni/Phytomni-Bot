# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite public contracts for the execution-keyed V2 journal.

This module is deliberately transport and Agent agnostic.  Runtime adapters
may construct these facts, but domain handlers do not own or extend the public
vocabulary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .execution_event_limits import DEFAULT_EXECUTION_EVENT_LIMITS
from .execution_stage_v2 import (
    ExecutionStageState,
    empty_execution_stage_state,
)
from .public_execution_safety import (
    PublicExecutionDataError,
    validate_public_execution_size,
    validate_public_execution_value,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
TerminalStatusV2 = Literal[
    "succeeded", "partial", "failed", "cancelled", "timed_out"
]


class ExecutionStatus(StrEnum):
    """Monotonic public execution lifecycle."""

    ADMITTED = "admitted"
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class SpanStatus(StrEnum):
    """Finite causal-span lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class WorkUnitStatus(StrEnum):
    """Stable logical-work lifecycle across retry attempts."""

    PENDING = "pending"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class EventStatus(StrEnum):
    """Finite status values admitted by a durable event envelope."""

    ADMITTED = "admitted"
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    PENDING = "pending"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    DEGRADED = "degraded"


class TrackingHealth(StrEnum):
    """Truthful availability of durable tracking facts."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    CONTRACT_DEGRADED = "contract_degraded"


class PublicTargetKind(StrEnum):
    """Opaque resources that Web must reauthorize before opening."""

    EVENT = "event"
    ARTIFACT = "artifact"
    REPORT = "report"
    TODO = "todo"
    PREVIEW = "preview"
    DOWNLOAD = "download"
    TRACE = "trace"


class ExecutionEventSource(StrEnum):
    """Shared seam that observed and committed an execution fact."""

    ADMISSION = "admission"
    RUNTIME = "runtime"
    DRIVER = "driver"
    GRAPH = "graph"
    TOOL = "tool"
    AGENT = "agent"
    PROVIDER = "provider"
    SUPERVISOR = "supervisor"
    CHECKPOINT = "checkpoint"
    ARTIFACT = "artifact"
    MESSAGE = "message"
    COMPATIBILITY = "compatibility"


class ExecutionEventType(StrEnum):
    """Finite durable public fact vocabulary."""

    EXECUTION_ADMITTED = "execution.admitted"
    EXECUTION_QUEUED = "execution.queued"
    EXECUTION_DISPATCHING = "execution.dispatching"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_WAITING_INPUT = "execution.waiting_input"
    EXECUTION_RESUMED = "execution.resumed"
    EXECUTION_CANCELLATION_REQUESTED = "execution.cancellation_requested"
    EXECUTION_SUCCEEDED = "execution.succeeded"
    EXECUTION_PARTIAL = "execution.partial"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_CANCELLED = "execution.cancelled"
    EXECUTION_TIMED_OUT = "execution.timed_out"
    SPAN_CREATED = "span.created"
    SPAN_STARTED = "span.started"
    SPAN_PROGRESS = "span.progress"
    SPAN_WAITING_INPUT = "span.waiting_input"
    SPAN_RESUMED = "span.resumed"
    SPAN_RETRY_SCHEDULED = "span.retry_scheduled"
    SPAN_SUCCEEDED = "span.succeeded"
    SPAN_PARTIAL = "span.partial"
    SPAN_FAILED = "span.failed"
    SPAN_CANCELLED = "span.cancelled"
    SPAN_TIMED_OUT = "span.timed_out"
    SPAN_SKIPPED = "span.skipped"
    WORK_UNIT_REGISTERED = "work_unit.registered"
    WORK_UNIT_ATTEMPT_STARTED = "work_unit.attempt_started"
    WORK_UNIT_SUBMITTED = "work_unit.submitted"
    WORK_UNIT_ACKNOWLEDGED = "work_unit.acknowledged"
    WORK_UNIT_PROGRESS = "work_unit.progress"
    WORK_UNIT_RETRY_SCHEDULED = "work_unit.retry_scheduled"
    WORK_UNIT_CANCELLATION_REQUESTED = "work_unit.cancellation_requested"
    WORK_UNIT_CANCELLATION_CONFIRMED = "work_unit.cancellation_confirmed"
    WORK_UNIT_SUCCEEDED = "work_unit.succeeded"
    WORK_UNIT_PARTIAL = "work_unit.partial"
    WORK_UNIT_FAILED = "work_unit.failed"
    WORK_UNIT_CANCELLED = "work_unit.cancelled"
    WORK_UNIT_TIMED_OUT = "work_unit.timed_out"
    TODO_SNAPSHOT = "todo.snapshot"
    REASONING_SUMMARY = "reasoning.summary"
    DECISION_NOTE = "decision.note"
    INPUT_REQUIRED = "input.required"
    INPUT_ACTION_CLAIMED = "input.action_claimed"
    INPUT_ACTION_REJECTED = "input.action_rejected"
    INPUT_RESOLVED = "input.resolved"
    ARTIFACT_PUBLISHED = "artifact.published"
    RESULT_PUBLISHED = "result.published"
    MESSAGE_SNAPSHOT = "message.snapshot"
    MESSAGE_COMPLETED = "message.completed"
    TRACKING_DEGRADED = "tracking.degraded"
    TRACKING_RECOVERED = "tracking.recovered"
    SEQUENCE_GAP = "sequence.gap"


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicSafeSummary(_PublicModel):
    """Bounded localization key plus a redacted text fallback."""

    key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=512)


class PublicTarget(_PublicModel):
    """Opaque target resolved under the authenticated owner."""

    kind: PublicTargetKind
    id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)


class OutputRevision(_PublicModel):
    """Resume coordinate for transient and durable assistant content."""

    revision: int = Field(ge=0)
    offset: int = Field(ge=0)


class ActionRevision(_PublicModel):
    """Optimistic revision required by input and cancellation actions."""

    expected_revision: int = Field(ge=0)


class EmptyPublicPayload(_PublicModel):
    """An event with no additional public metadata."""


class PhasePublicPayload(_PublicModel):
    """Stable semantic phase; private graph node names remain excluded."""

    phase: str = Field(min_length=1, max_length=128)
    duration_ms: int | None = Field(default=None, ge=0)


class ProgressPublicPayload(PhasePublicPayload):
    """Bounded exact progress for one public phase."""

    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    observation: Literal["liveness", "provider_contact"] | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_exact_progress_or_liveness(self) -> ProgressPublicPayload:
        """Require exact totals or an explicitly non-progress observation."""
        has_completed = self.completed is not None
        has_total = self.total is not None
        if has_completed != has_total:
            raise ValueError("progress_counters_incomplete")
        if has_completed:
            assert self.completed is not None and self.total is not None
            if self.completed > self.total:
                raise ValueError("progress_exceeds_total")
            if self.observation is not None or self.elapsed_ms is not None:
                raise ValueError("progress_observation_conflict")
            if self.unit is not None:
                from .execution_trace_detail import (
                    OPERATION_PRESENTER_REGISTRY,
                )

                presented = OPERATION_PRESENTER_REGISTRY.present(
                    self.phase,
                    progress={
                        "completed": self.completed,
                        "total": self.total,
                        "unit": self.unit,
                    },
                )
                if presented.progress != {
                    "completed": self.completed,
                    "total": self.total,
                    "unit": self.unit,
                }:
                    raise ValueError("unsafe_progress_unit")
            return self
        if (
            self.observation is None
            or self.elapsed_ms is None
            or self.unit is not None
        ):
            raise ValueError("liveness_observation_required")
        return self


class FailurePublicPayload(_PublicModel):
    """Stable public failure classification."""

    code: str = Field(min_length=1, max_length=128)
    retryable: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    work_unit_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )


class RetryPublicPayload(_PublicModel):
    """Bounded retry schedule without provider internals."""

    delay_ms: int = Field(ge=0)
    next_attempt: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=128)


class WorkUnitPublicPayload(_PublicModel):
    """Public logical-operation identity and optional source revision."""

    operation_key: str = Field(min_length=1, max_length=128)
    source_revision: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    detail: dict[str, int | bool | str] | None = Field(
        default=None,
        max_length=16,
    )

    @model_validator(mode="after")
    def validate_presenter_detail(self) -> WorkUnitPublicPayload:
        """Reject fields or values outside the operation's finite schema."""
        if self.detail is None:
            return self
        from .execution_trace_detail import OPERATION_PRESENTER_REGISTRY

        presented = OPERATION_PRESENTER_REGISTRY.present(
            self.operation_key,
            detail=self.detail,
        )
        if presented.operation_key != self.operation_key:
            raise ValueError("unknown_operation_detail")
        if presented.detail != self.detail:
            raise ValueError("unsafe_operation_detail")
        return self


class PublicTodoItemV2(_PublicModel):
    """One item in an atomic declared Todo snapshot."""

    id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    label_key: str = Field(min_length=1, max_length=128)
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"]


class TodoSnapshotPublicPayload(_PublicModel):
    """Whole-plan replacement; absence means no plan was declared."""

    items: tuple[PublicTodoItemV2, ...] = Field(max_length=100)


class PublicTextPayload(_PublicModel):
    """Explicitly public summary, never raw model chain-of-thought."""

    text: str = Field(min_length=1, max_length=512)


class InputRequiredPublicPayload(_PublicModel):
    """Safe identity of an interactive surface."""

    surface_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    widget: str = Field(min_length=1, max_length=64)
    action_revision: int = Field(ge=0)


class InputResolvedPublicPayload(_PublicModel):
    """Bounded outcome of one claimed input surface."""

    surface_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    outcome: str = Field(min_length=1, max_length=64)
    action_revision: int = Field(ge=0)


class ResourcePublishedPublicPayload(_PublicModel):
    """Public resource metadata; content remains outside the journal."""

    name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)


class CitationReferencePublicPayload(_PublicModel):
    """One finite bibliographic row; clients derive approved resolver URLs."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    au: str | None = Field(default=None, min_length=1, max_length=512)
    ti: str | None = Field(default=None, min_length=1, max_length=512)
    so: str | None = Field(default=None, min_length=1, max_length=512)
    vl: str | None = Field(default=None, min_length=1, max_length=512)
    bp: str | None = Field(default=None, min_length=1, max_length=512)
    ep: str | None = Field(default=None, min_length=1, max_length=512)
    ar: str | None = Field(default=None, min_length=1, max_length=512)
    py: str | None = Field(default=None, min_length=1, max_length=512)
    di: str | None = Field(default=None, min_length=1, max_length=512)
    pm: str | None = Field(default=None, min_length=1, max_length=512)
    doi_missing: bool | None = None


class MessagePublicPayload(_PublicModel):
    """One contiguous durable assistant-content chunk.

    A bounded event is never allowed to masquerade as the complete answer.
    Stable message identity plus offsets, count, total length, and digest let
    Web reconstruct the exact visible message without transient token frames.
    """

    output_revision: int = Field(ge=0)
    message_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    source_message_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    base_offset: int = Field(ge=0)
    offset: int = Field(ge=0)
    total_length: int = Field(ge=0)
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(max_length=8192)
    references: tuple[CitationReferencePublicPayload, ...] | None = Field(
        default=None,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_contiguous_chunk(self) -> MessagePublicPayload:
        if self.offset != self.base_offset + len(self.text):
            raise ValueError("message chunk offset mismatch")
        if self.offset > self.total_length:
            raise ValueError("message chunk exceeds total length")
        if self.chunk_index >= self.chunk_count:
            raise ValueError("message chunk index out of range")
        return self


class TrackingPublicPayload(_PublicModel):
    """Finite tracking-health transition."""

    health: TrackingHealth
    code: str | None = Field(default=None, min_length=1, max_length=128)
    retryable: bool = False


class CancellationPublicPayload(_PublicModel):
    """Finite cancellation state without transport ambiguity."""

    outcome: Literal["requested", "confirmed", "best_effort", "unsupported"]


class SequenceGapPublicPayload(_PublicModel):
    """Explicit retained-history discontinuity."""

    first_available_seq: int = Field(ge=1)
    last_pruned_seq: int = Field(ge=0)


PublicPayloadV2 = (
    EmptyPublicPayload
    | PhasePublicPayload
    | ProgressPublicPayload
    | FailurePublicPayload
    | RetryPublicPayload
    | WorkUnitPublicPayload
    | TodoSnapshotPublicPayload
    | PublicTextPayload
    | InputRequiredPublicPayload
    | InputResolvedPublicPayload
    | ResourcePublishedPublicPayload
    | MessagePublicPayload
    | TrackingPublicPayload
    | CancellationPublicPayload
    | SequenceGapPublicPayload
)

_EMPTY_TYPES = {
    ExecutionEventType.EXECUTION_ADMITTED,
    ExecutionEventType.EXECUTION_QUEUED,
    ExecutionEventType.EXECUTION_DISPATCHING,
    ExecutionEventType.EXECUTION_STARTED,
    ExecutionEventType.EXECUTION_WAITING_INPUT,
    ExecutionEventType.EXECUTION_RESUMED,
    ExecutionEventType.EXECUTION_SUCCEEDED,
}
_PHASE_TYPES = {
    ExecutionEventType.SPAN_CREATED,
    ExecutionEventType.SPAN_STARTED,
    ExecutionEventType.SPAN_WAITING_INPUT,
    ExecutionEventType.SPAN_RESUMED,
    ExecutionEventType.SPAN_SUCCEEDED,
    ExecutionEventType.SPAN_CANCELLED,
    ExecutionEventType.SPAN_SKIPPED,
}
_FAILURE_TYPES = {
    ExecutionEventType.EXECUTION_PARTIAL,
    ExecutionEventType.EXECUTION_FAILED,
    ExecutionEventType.EXECUTION_TIMED_OUT,
    ExecutionEventType.SPAN_PARTIAL,
    ExecutionEventType.SPAN_FAILED,
    ExecutionEventType.SPAN_TIMED_OUT,
    ExecutionEventType.WORK_UNIT_PARTIAL,
    ExecutionEventType.WORK_UNIT_FAILED,
    ExecutionEventType.WORK_UNIT_TIMED_OUT,
}
_WORK_UNIT_TYPES = {
    ExecutionEventType.WORK_UNIT_REGISTERED,
    ExecutionEventType.WORK_UNIT_ATTEMPT_STARTED,
    ExecutionEventType.WORK_UNIT_SUBMITTED,
    ExecutionEventType.WORK_UNIT_ACKNOWLEDGED,
    ExecutionEventType.WORK_UNIT_SUCCEEDED,
    ExecutionEventType.WORK_UNIT_CANCELLED,
}

_PAYLOAD_MODELS: dict[ExecutionEventType, type[BaseModel]] = {
    **{event_type: EmptyPublicPayload for event_type in _EMPTY_TYPES},
    **{event_type: PhasePublicPayload for event_type in _PHASE_TYPES},
    **{event_type: FailurePublicPayload for event_type in _FAILURE_TYPES},
    **{event_type: WorkUnitPublicPayload for event_type in _WORK_UNIT_TYPES},
    ExecutionEventType.EXECUTION_CANCELLATION_REQUESTED: CancellationPublicPayload,
    ExecutionEventType.EXECUTION_CANCELLED: CancellationPublicPayload,
    ExecutionEventType.SPAN_PROGRESS: ProgressPublicPayload,
    ExecutionEventType.SPAN_RETRY_SCHEDULED: RetryPublicPayload,
    ExecutionEventType.WORK_UNIT_PROGRESS: ProgressPublicPayload,
    ExecutionEventType.WORK_UNIT_RETRY_SCHEDULED: RetryPublicPayload,
    ExecutionEventType.WORK_UNIT_CANCELLATION_REQUESTED: CancellationPublicPayload,
    ExecutionEventType.WORK_UNIT_CANCELLATION_CONFIRMED: CancellationPublicPayload,
    ExecutionEventType.TODO_SNAPSHOT: TodoSnapshotPublicPayload,
    ExecutionEventType.REASONING_SUMMARY: PublicTextPayload,
    ExecutionEventType.DECISION_NOTE: PublicTextPayload,
    ExecutionEventType.INPUT_REQUIRED: InputRequiredPublicPayload,
    ExecutionEventType.INPUT_ACTION_CLAIMED: InputResolvedPublicPayload,
    ExecutionEventType.INPUT_ACTION_REJECTED: InputResolvedPublicPayload,
    ExecutionEventType.INPUT_RESOLVED: InputResolvedPublicPayload,
    ExecutionEventType.ARTIFACT_PUBLISHED: ResourcePublishedPublicPayload,
    ExecutionEventType.RESULT_PUBLISHED: ResourcePublishedPublicPayload,
    ExecutionEventType.MESSAGE_SNAPSHOT: MessagePublicPayload,
    ExecutionEventType.MESSAGE_COMPLETED: MessagePublicPayload,
    ExecutionEventType.TRACKING_DEGRADED: TrackingPublicPayload,
    ExecutionEventType.TRACKING_RECOVERED: TrackingPublicPayload,
    ExecutionEventType.SEQUENCE_GAP: SequenceGapPublicPayload,
}


class ExecutionEventV2(_PublicModel):
    """Immutable execution-keyed, span-aware public fact."""

    schema_version: Literal[2]
    event_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    execution_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    seq: int = Field(ge=1)
    type: ExecutionEventType
    status: EventStatus
    occurred_at: str = Field(min_length=1, max_length=64)
    source: ExecutionEventSource
    span_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    parent_span_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    work_unit_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    attempt: int = Field(ge=1)
    summary: PublicSafeSummary
    public_payload: PublicPayloadV2
    target: PublicTarget | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize the strict public envelope."""
        encoded = self.model_dump(mode="json")
        encoded["public_payload"] = self.public_payload.model_dump(
            mode="json",
            exclude_none=True,
        )
        return encoded


class ExecutionEventIntentV2(_PublicModel):
    """Producer fact before the journal allocates identity and sequence."""

    type: ExecutionEventType
    status: EventStatus
    source: ExecutionEventSource
    span_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    parent_span_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    work_unit_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    attempt: int = Field(default=1, ge=1)
    summary: PublicSafeSummary
    public_payload: PublicPayloadV2
    target: PublicTarget | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    def materialize(
        self,
        *,
        execution_id: str,
        seq: int,
        event_id: str,
        occurred_at: str,
    ) -> ExecutionEventV2:
        """Attach journal-owned ordering and stable identity."""
        raw = self.model_dump(mode="json")
        raw.update(
            {
                "schema_version": 2,
                "execution_id": execution_id,
                "seq": seq,
                "event_id": event_id,
                "occurred_at": occurred_at,
            }
        )
        return parse_execution_event_v2(raw)


class PublicResultItemV2(_PublicModel):
    """One result inventory entry backed by an authorized target."""

    id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    target: PublicTarget


class PublicWarningV2(_PublicModel):
    """Stable partial/failure warning without private exception text."""

    code: str = Field(min_length=1, max_length=128)
    work_unit_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )


class TerminalOutcomeV2(_PublicModel):
    """Sticky terminal state reconstructed from the journal."""

    status: TerminalStatusV2
    event_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    result_revision: int = Field(default=0, ge=0)


OperationStatusV2 = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
]
OperationAttemptStatusV2 = Literal[
    "running",
    "retry_scheduled",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]


class PublicOperationFailureV2(_PublicModel):
    """Stable failure classification for one operation attempt."""

    code: str = Field(min_length=1, max_length=128)
    retryable: bool = False


class PublicOperationRetryV2(_PublicModel):
    """Finite retry delay for one operation attempt."""

    delay_ms: int = Field(ge=0)


class PublicOperationAttemptV2(_PublicModel):
    """Bounded lifecycle detail for one numbered attempt."""

    attempt: int = Field(ge=1)
    status: OperationAttemptStatusV2
    started_at: str
    completed_at: str | None = None
    duration_ms: int = Field(ge=0)
    failure: PublicOperationFailureV2 | None = None
    retry: PublicOperationRetryV2 | None = None


class PublicOperationProgressV2(_PublicModel):
    """Exact measurable progress; no inferred percentage is stored."""

    completed: int = Field(ge=0)
    total: int = Field(ge=1)
    unit: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_progress(self) -> PublicOperationProgressV2:
        if self.completed > self.total:
            raise ValueError("progress_exceeds_total")
        return self


class PublicOperationSummaryV2(_PublicModel):
    """Explicitly safe operation/decision/reasoning summary."""

    kind: Literal["operation", "decision", "reasoning"]
    text: str = Field(min_length=1, max_length=512)


class PublicOperationRecordV2(_PublicModel):
    """One grouped operation row keyed by its durable work-unit identity."""

    schema_version: Literal[1] = 1
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    work_unit_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    operation_key: str = Field(min_length=1, max_length=128)
    label_key: str = Field(min_length=1, max_length=128)
    fallback_label: str = Field(min_length=1, max_length=128)
    status: OperationStatusV2
    started_at: str
    last_observation_at: str
    completed_at: str | None = None
    duration_ms: int = Field(ge=0)
    current_attempt: int = Field(ge=1)
    attempts: tuple[PublicOperationAttemptV2, ...] = Field(max_length=8)
    progress: PublicOperationProgressV2 | None = None
    detail: dict[str, int | bool | str] = Field(max_length=16)
    summary: PublicOperationSummaryV2 | None = None
    target: PublicTarget | None = None


class ExecutionContextStageV2(_PublicModel):
    """Bounded signal that Web may acknowledge after message persistence."""

    schema_version: Literal[1] = 1
    turn_id: str = Field(min_length=1, max_length=128)
    selected_agent_id: str = Field(min_length=1, max_length=128)
    route_source: Literal["instant_lock", "explicit_selection", "router"]
    route_reason_code: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    base_business_context_version: int = Field(ge=0)
    proposed_business_context_version: int = Field(ge=0)
    last_applied_ledger_cursor: int = Field(ge=0)
    context_truncated: bool
    context_rebuilt: bool


class ExecutionProjectionV2(_PublicModel):
    """Rebuildable bounded read cache for one execution."""

    schema_version: Literal[2] = 2
    execution_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    agent_slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    status: ExecutionStatus
    latest_seq: int = Field(ge=0)
    output_revision: int = Field(default=0, ge=0)
    output_offset: int = Field(default=0, ge=0)
    operation_revision: int = Field(default=0, ge=0)
    operations: tuple[PublicOperationRecordV2, ...] = Field(
        default=(), max_length=256
    )
    execution_stage: ExecutionStageState = Field(
        default_factory=empty_execution_stage_state
    )
    tracking_health: TrackingHealth = TrackingHealth.HEALTHY
    active_span_ids: tuple[str, ...] = ()
    todo_declared: bool = False
    todos: tuple[PublicTodoItemV2, ...] = ()
    results: tuple[PublicResultItemV2, ...] = ()
    targets: tuple[PublicTarget, ...] = ()
    failed_work_unit_ids: tuple[str, ...] = ()
    warnings: tuple[PublicWarningV2, ...] = ()
    input_required: InputRequiredPublicPayload | None = None
    context_stage: ExecutionContextStageV2 | None = None
    terminal: TerminalOutcomeV2 | None = None


class ExecutionJournalValidationError(ValueError):
    """A V2 event failed finite public contract validation."""


def _fail(reason: str) -> NoReturn:
    raise ExecutionJournalValidationError(reason)


def _validate_utc(value: object) -> None:
    if not isinstance(value, str):
        _fail("invalid_occurred_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_occurred_at")
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        _fail("invalid_occurred_at")


def _validate_public_event_value(
    raw: dict[str, Any],
    event_type: ExecutionEventType,
) -> None:
    """Apply one safety policy to stored events and producer intents."""
    safety_value: dict[str, Any] = raw
    if event_type in {
        ExecutionEventType.MESSAGE_SNAPSHOT,
        ExecutionEventType.MESSAGE_COMPLETED,
    }:
        safety_value = dict(raw)
        payload = raw.get("public_payload")
        if not isinstance(payload, Mapping):
            _fail("invalid_public_payload")
        payload_for_safety = dict(payload)
        validate_public_execution_value(
            payload_for_safety.get("text"),
            max_string_chars=8192,
        )
        # The strict message model validates its event-sized chunk. Keep the
        # generic 512-character limit for every other public string.
        payload_for_safety["text"] = ""
        safety_value["public_payload"] = payload_for_safety
    validate_public_execution_value(
        safety_value,
        max_string_chars=DEFAULT_EXECUTION_EVENT_LIMITS.max_summary_chars,
    )


def parse_execution_event_v2(value: object) -> ExecutionEventV2:
    """Decode one required V2 fact through strict typing and redaction."""
    if not isinstance(value, Mapping):
        _fail("invalid_event")
    raw = dict(value)
    event_type_value = raw.get("type")
    if not isinstance(event_type_value, str):
        _fail("unknown_required_event_type")
    try:
        event_type = ExecutionEventType(event_type_value)
    except ValueError:
        _fail("unknown_required_event_type")
    try:
        _validate_public_event_value(raw, event_type)
    except PublicExecutionDataError as exc:
        raise ExecutionJournalValidationError(str(exc)) from exc
    payload_model = _PAYLOAD_MODELS[event_type]
    try:
        raw["public_payload"] = payload_model.model_validate(
            raw.get("public_payload")
        )
    except ValidationError as exc:
        raise ExecutionJournalValidationError(
            "invalid_public_payload"
        ) from exc
    _validate_utc(raw.get("occurred_at"))
    try:
        event = ExecutionEventV2.model_validate(raw)
    except ValidationError as exc:
        raise ExecutionJournalValidationError("invalid_event") from exc
    try:
        encoded = json.dumps(event.to_public_dict(), ensure_ascii=False)
        validate_public_execution_size(
            encoded,
            max_bytes=DEFAULT_EXECUTION_EVENT_LIMITS.max_event_bytes,
        )
    except PublicExecutionDataError as exc:
        raise ExecutionJournalValidationError(str(exc)) from exc
    return event


def parse_execution_event_intent_v2(value: object) -> ExecutionEventIntentV2:
    """Decode a producer intent through the same finite payload contract."""
    if not isinstance(value, Mapping):
        _fail("invalid_event_intent")
    raw = dict(value)
    event_type_value = raw.get("type")
    if not isinstance(event_type_value, str):
        _fail("unknown_required_event_type")
    try:
        event_type = ExecutionEventType(event_type_value)
    except ValueError:
        _fail("unknown_required_event_type")
    try:
        _validate_public_event_value(raw, event_type)
    except PublicExecutionDataError as exc:
        raise ExecutionJournalValidationError(str(exc)) from exc
    payload_model = _PAYLOAD_MODELS[event_type]
    try:
        raw["public_payload"] = payload_model.model_validate(
            raw.get("public_payload")
        )
        return ExecutionEventIntentV2.model_validate(raw)
    except ValidationError as exc:
        raise ExecutionJournalValidationError("invalid_event_intent") from exc
