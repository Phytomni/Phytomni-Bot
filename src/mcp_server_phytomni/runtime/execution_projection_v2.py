# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic reducer for the execution journal V2 public projection."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import cast

from .execution_event_limits import DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS
from .execution_journal_v2 import (
    ExecutionEventType,
    ExecutionEventV2,
    ExecutionProjectionV2,
    ExecutionStatus,
    FailurePublicPayload,
    InputRequiredPublicPayload,
    MessagePublicPayload,
    OperationAttemptStatusV2,
    OperationStatusV2,
    PhasePublicPayload,
    ProgressPublicPayload,
    PublicOperationAttemptV2,
    PublicOperationFailureV2,
    PublicOperationProgressV2,
    PublicOperationRecordV2,
    PublicOperationRetryV2,
    PublicOperationSummaryV2,
    PublicResultItemV2,
    PublicTarget,
    PublicTargetKind,
    PublicWarningV2,
    ResourcePublishedPublicPayload,
    RetryPublicPayload,
    TerminalOutcomeV2,
    TerminalStatusV2,
    TodoSnapshotPublicPayload,
    TrackingPublicPayload,
    WorkUnitPublicPayload,
)
from .execution_progress_v2 import operation_progress
from .execution_stage_v2 import (
    ExecutionStage,
    ExecutionStageSignal,
    ExecutionStageState,
    reduce_execution_stage,
)
from .execution_trace_detail import OPERATION_PRESENTER_REGISTRY

_EXECUTION_STATUS_BY_TYPE = {
    ExecutionEventType.EXECUTION_ADMITTED: ExecutionStatus.ADMITTED,
    ExecutionEventType.EXECUTION_QUEUED: ExecutionStatus.QUEUED,
    ExecutionEventType.EXECUTION_DISPATCHING: ExecutionStatus.DISPATCHING,
    ExecutionEventType.EXECUTION_STARTED: ExecutionStatus.RUNNING,
    ExecutionEventType.EXECUTION_WAITING_INPUT: ExecutionStatus.WAITING_INPUT,
    ExecutionEventType.EXECUTION_RESUMED: ExecutionStatus.RUNNING,
}
_TERMINAL_STATUS_BY_TYPE = {
    ExecutionEventType.EXECUTION_SUCCEEDED: ExecutionStatus.SUCCEEDED,
    ExecutionEventType.EXECUTION_PARTIAL: ExecutionStatus.PARTIAL,
    ExecutionEventType.EXECUTION_FAILED: ExecutionStatus.FAILED,
    ExecutionEventType.EXECUTION_CANCELLED: ExecutionStatus.CANCELLED,
    ExecutionEventType.EXECUTION_TIMED_OUT: ExecutionStatus.TIMED_OUT,
}
_SPAN_START_TYPES = {
    ExecutionEventType.SPAN_CREATED,
    ExecutionEventType.SPAN_STARTED,
    ExecutionEventType.SPAN_RESUMED,
    ExecutionEventType.SPAN_WAITING_INPUT,
}
_SPAN_END_TYPES = {
    ExecutionEventType.SPAN_SUCCEEDED,
    ExecutionEventType.SPAN_PARTIAL,
    ExecutionEventType.SPAN_FAILED,
    ExecutionEventType.SPAN_CANCELLED,
    ExecutionEventType.SPAN_TIMED_OUT,
    ExecutionEventType.SPAN_SKIPPED,
}
_TERMINAL_STAGE_ROOT_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "cancelled", "timed_out"}
)
_TERMINAL_STAGE_SIGNAL_BY_TYPE = {
    ExecutionEventType.EXECUTION_SUCCEEDED: (
        ExecutionStageSignal.ROOT_SUCCEEDED
    ),
    ExecutionEventType.EXECUTION_PARTIAL: ExecutionStageSignal.ROOT_PARTIAL,
    ExecutionEventType.EXECUTION_FAILED: ExecutionStageSignal.ROOT_FAILED,
    ExecutionEventType.EXECUTION_CANCELLED: (
        ExecutionStageSignal.ROOT_CANCELLED
    ),
    ExecutionEventType.EXECUTION_TIMED_OUT: (
        ExecutionStageSignal.ROOT_TIMED_OUT
    ),
}
_STAGE_BY_SIGNAL = {
    ExecutionStageSignal.PROVIDER_SUBMITTED: ExecutionStage.ORCHESTRATION,
    ExecutionStageSignal.SCIENTIFIC_EXECUTION_STARTED: (
        ExecutionStage.SCIENTIFIC_EXECUTION
    ),
    ExecutionStageSignal.CONSOLIDATION_STARTED: ExecutionStage.CONSOLIDATION,
    ExecutionStageSignal.ANSWER_AVAILABLE: ExecutionStage.RESPONSE_SETTLEMENT,
}
_ACTIVE_PROVIDER_STATES = frozenset(
    {"queued", "running", "consolidating", "terminal"}
)


def empty_execution_projection_v2(execution_id: str) -> ExecutionProjectionV2:
    """Return the admitted, event-less public projection."""
    return ExecutionProjectionV2(
        execution_id=execution_id,
        status=ExecutionStatus.ADMITTED,
        latest_seq=0,
    )


def apply_execution_event_v2(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Apply one committed later fact without consulting mutable state."""
    if event.execution_id != projection.execution_id:
        raise ValueError("projection_execution_mismatch")
    if event.seq <= projection.latest_seq:
        raise ValueError("projection_sequence_mismatch")
    projection = projection.model_copy(
        update={
            "latest_seq": event.seq,
            "operations": _apply_operation_event(projection.operations, event),
            "execution_stage": _apply_stage_event(
                projection.execution_stage, event
            ),
        }
    )
    reducers = (
        _apply_execution_lifecycle,
        _apply_todo_snapshot,
        _apply_published_target,
        _apply_output_position,
        _apply_tracking_health,
        _apply_failure_warning,
    )
    for reducer in reducers:
        projection = reducer(projection, event)
    return _validated_projection(_align_terminal_result_revision(projection))


def _validated_projection(
    projection: ExecutionProjectionV2,
) -> ExecutionProjectionV2:
    """Revalidate the reduced state with the legacy persisted field set."""
    return ExecutionProjectionV2.model_validate(
        projection.model_dump(exclude={"run_id"})
    )


def _apply_execution_lifecycle(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Apply root status, input, span, and terminal lifecycle facts."""
    if projection.terminal is not None:
        return projection
    updates: dict[str, object] = {
        "status": _EXECUTION_STATUS_BY_TYPE.get(event.type, projection.status)
    }
    if event.type is ExecutionEventType.INPUT_REQUIRED:
        updates["status"] = ExecutionStatus.WAITING_INPUT
        if isinstance(event.public_payload, InputRequiredPublicPayload):
            updates["input_required"] = event.public_payload
            updates["operation_revision"] = max(
                projection.operation_revision,
                event.public_payload.action_revision,
            )
    elif event.type is ExecutionEventType.INPUT_RESOLVED:
        updates.update(
            status=ExecutionStatus.RUNNING,
            input_required=None,
        )
    if event.type in _SPAN_START_TYPES:
        updates["active_span_ids"] = _append_unique(
            projection.active_span_ids, event.span_id
        )
    elif event.type in _SPAN_END_TYPES:
        updates["active_span_ids"] = tuple(
            span_id
            for span_id in projection.active_span_ids
            if span_id != event.span_id
        )
    terminal_status = _TERMINAL_STATUS_BY_TYPE.get(event.type)
    if terminal_status is not None:
        updates.update(
            status=terminal_status,
            active_span_ids=(),
            terminal=TerminalOutcomeV2(
                status=cast(TerminalStatusV2, terminal_status.value),
                event_id=event.event_id,
                result_revision=projection.output_revision,
            ),
        )
    return projection.model_copy(update=updates)


def _apply_todo_snapshot(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Replace the public todo snapshot when the event carries one."""
    if not isinstance(event.public_payload, TodoSnapshotPublicPayload):
        return projection
    return projection.model_copy(
        update={
            "todo_declared": True,
            "todos": event.public_payload.items,
        }
    )


def _apply_published_target(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Append a unique target and its bounded public result item."""
    if event.target is None:
        return projection
    updates: dict[str, object] = {
        "targets": _append_target(projection.targets, event.target)
    }
    if (
        isinstance(event.public_payload, ResourcePublishedPublicPayload)
        and event.target.kind is not PublicTargetKind.TRACE
        and all(result.id != event.event_id for result in projection.results)
    ):
        updates["results"] = (
            *projection.results,
            PublicResultItemV2(
                id=event.event_id,
                name=event.public_payload.name,
                media_type=event.public_payload.media_type,
                size_bytes=event.public_payload.size_bytes,
                target=event.target,
            ),
        )
    return projection.model_copy(update=updates)


def _apply_output_position(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Advance the monotonic public message cursor."""
    if not isinstance(event.public_payload, MessagePublicPayload):
        return projection
    incoming = (
        event.public_payload.output_revision,
        event.public_payload.offset,
    )
    if incoming < (projection.output_revision, projection.output_offset):
        return projection
    return projection.model_copy(
        update={
            "output_revision": incoming[0],
            "output_offset": incoming[1],
        }
    )


def _apply_tracking_health(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Apply an explicit public tracking-health observation."""
    if not isinstance(event.public_payload, TrackingPublicPayload):
        return projection
    return projection.model_copy(
        update={"tracking_health": event.public_payload.health}
    )


def _apply_failure_warning(
    projection: ExecutionProjectionV2,
    event: ExecutionEventV2,
) -> ExecutionProjectionV2:
    """Record one unique failure warning and affected work unit."""
    if not isinstance(event.public_payload, FailurePublicPayload):
        return projection
    work_unit_id = event.public_payload.work_unit_id or event.work_unit_id
    failed_work_units = projection.failed_work_unit_ids
    if work_unit_id is not None:
        failed_work_units = _append_unique(failed_work_units, work_unit_id)
    warning = PublicWarningV2(
        code=event.public_payload.code,
        work_unit_id=work_unit_id,
    )
    warnings = projection.warnings
    if warning not in warnings:
        warnings = (*warnings, warning)
    return projection.model_copy(
        update={
            "failed_work_unit_ids": failed_work_units,
            "warnings": warnings,
        }
    )


def _align_terminal_result_revision(
    projection: ExecutionProjectionV2,
) -> ExecutionProjectionV2:
    """Advance terminal result visibility after a later message fact."""
    terminal = projection.terminal
    if (
        terminal is None
        or terminal.result_revision >= projection.output_revision
    ):
        return projection
    return projection.model_copy(
        update={
            "terminal": terminal.model_copy(
                update={"result_revision": projection.output_revision}
            )
        }
    )


def fold_execution_events_v2(
    execution_id: str,
    events: Iterable[ExecutionEventV2],
) -> ExecutionProjectionV2:
    """Rebuild an identical projection from ordered retained facts."""
    projection = empty_execution_projection_v2(execution_id)
    for event in events:
        projection = apply_execution_event_v2(projection, event)
    return projection


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _append_target(
    values: tuple[PublicTarget, ...], target: PublicTarget
) -> tuple[PublicTarget, ...]:
    return values if target in values else (*values, target)


_OPERATION_TERMINAL_STATUS = {
    ExecutionEventType.WORK_UNIT_SUCCEEDED: "succeeded",
    ExecutionEventType.SPAN_SUCCEEDED: "succeeded",
    ExecutionEventType.WORK_UNIT_PARTIAL: "partial",
    ExecutionEventType.SPAN_PARTIAL: "partial",
    ExecutionEventType.WORK_UNIT_FAILED: "failed",
    ExecutionEventType.SPAN_FAILED: "failed",
    ExecutionEventType.WORK_UNIT_CANCELLED: "cancelled",
    ExecutionEventType.SPAN_CANCELLED: "cancelled",
    ExecutionEventType.WORK_UNIT_TIMED_OUT: "timed_out",
    ExecutionEventType.SPAN_TIMED_OUT: "timed_out",
}


def _apply_operation_event(
    operations: tuple[PublicOperationRecordV2, ...],
    event: ExecutionEventV2,
) -> tuple[PublicOperationRecordV2, ...]:
    work_unit_id = event.work_unit_id
    if work_unit_id is None or not event.type.value.startswith(
        ("work_unit.", "span.")
    ):
        return operations
    existing_index = _operation_index(operations, work_unit_id)
    existing = (
        operations[existing_index] if existing_index is not None else None
    )
    operation = _build_operation(event, existing, work_unit_id)
    return _replace_or_append_operation(operations, operation, existing_index)


def _operation_index(
    operations: tuple[PublicOperationRecordV2, ...],
    work_unit_id: str,
) -> int | None:
    """Return the existing row index for a durable work unit."""
    return next(
        (
            index
            for index, operation in enumerate(operations)
            if operation.work_unit_id == work_unit_id
        ),
        None,
    )


def _build_operation(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
    work_unit_id: str,
) -> PublicOperationRecordV2:
    """Build one operation row from the latest immutable event fact."""
    operation_key = _operation_key(event, existing)
    presenter = OPERATION_PRESENTER_REGISTRY.resolve(operation_key)
    started_at = existing.started_at if existing else event.occurred_at
    return PublicOperationRecordV2(
        operation_id=_operation_id(existing, work_unit_id),
        work_unit_id=work_unit_id,
        operation_key=presenter.operation_key,
        label_key=presenter.label_key,
        fallback_label=presenter.fallback_label,
        status=_operation_status(event, existing),
        started_at=started_at,
        last_observation_at=event.occurred_at,
        completed_at=_operation_completed_at(event, existing),
        duration_ms=_operation_duration(event, existing, started_at),
        current_attempt=max(
            event.attempt,
            existing.current_attempt if existing is not None else 1,
        ),
        attempts=_apply_attempt(existing, event, started_at),
        progress=_resolved_operation_progress(
            event, existing, presenter.counter_units
        ),
        detail=_operation_detail(event, existing),
        summary=_operation_summary(event, existing),
        target=event.target
        or (existing.target if existing is not None else None),
    )


def _operation_id(
    existing: PublicOperationRecordV2 | None,
    work_unit_id: str,
) -> str:
    """Reuse the stable operation id or derive it from the work unit."""
    if existing is not None:
        return existing.operation_id
    digest = hashlib.sha256(work_unit_id.encode("utf-8")).hexdigest()[:32]
    return f"operation-{digest}"


def _operation_completed_at(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
) -> str | None:
    """Resolve completion time only from terminal operation facts."""
    if event.type in _OPERATION_TERMINAL_STATUS:
        return event.occurred_at
    return existing.completed_at if existing is not None else None


def _operation_duration(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
    started_at: str,
) -> int:
    """Keep non-terminal duration observations monotonic."""
    duration_ms = _duration_ms(event, started_at)
    if existing is not None and event.type not in _OPERATION_TERMINAL_STATUS:
        return max(existing.duration_ms, duration_ms)
    return duration_ms


def _resolved_operation_progress(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
    counter_units: tuple[str, ...],
) -> PublicOperationProgressV2 | None:
    """Apply explicit progress or retain the latest measured value."""
    progress = operation_progress(event.public_payload, counter_units)
    if progress is None and existing is not None:
        return existing.progress
    return progress


def _operation_detail(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
) -> dict[str, int | bool | str]:
    """Project the latest explicitly safe operation detail."""
    payload = event.public_payload
    if (
        isinstance(payload, WorkUnitPublicPayload)
        and payload.detail is not None
    ):
        return payload.detail
    return existing.detail if existing is not None else {}


def _operation_summary(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
) -> PublicOperationSummaryV2 | None:
    """Replace summaries only at retry or terminal boundaries."""
    if event.type in _OPERATION_TERMINAL_STATUS or event.type is (
        ExecutionEventType.WORK_UNIT_RETRY_SCHEDULED
    ):
        return PublicOperationSummaryV2(
            kind="operation", text=event.summary.text
        )
    return existing.summary if existing is not None else None


def _replace_or_append_operation(
    operations: tuple[PublicOperationRecordV2, ...],
    operation: PublicOperationRecordV2,
    existing_index: int | None,
) -> tuple[PublicOperationRecordV2, ...]:
    """Replace a known row or append within the configured trace bound."""
    if existing_index is not None:
        mutable = list(operations)
        mutable[existing_index] = operation
        return tuple(mutable)
    if (
        len(operations)
        >= DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.max_operations_per_run
    ):
        return operations
    return (*operations, operation)


def _operation_key(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
) -> str:
    payload = event.public_payload
    candidate: str | None = None
    if isinstance(payload, WorkUnitPublicPayload):
        candidate = payload.operation_key
    elif isinstance(payload, PhasePublicPayload):
        candidate = payload.phase
    if candidate is not None:
        presenter = OPERATION_PRESENTER_REGISTRY.resolve(candidate)
        if presenter.operation_key == candidate:
            return candidate
    return (
        existing.operation_key if existing is not None else "operation.unknown"
    )


def _operation_status(
    event: ExecutionEventV2,
    existing: PublicOperationRecordV2 | None,
) -> OperationStatusV2:
    terminal = _OPERATION_TERMINAL_STATUS.get(event.type)
    if terminal is not None:
        return cast(OperationStatusV2, terminal)
    if event.type is ExecutionEventType.WORK_UNIT_RETRY_SCHEDULED:
        return "retrying"
    if event.type is ExecutionEventType.WORK_UNIT_REGISTERED:
        return "queued"
    return (
        existing.status
        if existing is not None and existing.completed_at
        else "running"
    )


def _apply_attempt(
    existing: PublicOperationRecordV2 | None,
    event: ExecutionEventV2,
    operation_started_at: str,
) -> tuple[PublicOperationAttemptV2, ...]:
    attempts = list(existing.attempts if existing is not None else ())
    index = next(
        (
            index
            for index, attempt in enumerate(attempts)
            if attempt.attempt == event.attempt
        ),
        None,
    )
    prior = attempts[index] if index is not None else None
    terminal_status = _OPERATION_TERMINAL_STATUS.get(event.type)
    if event.type is ExecutionEventType.WORK_UNIT_RETRY_SCHEDULED:
        status = "retry_scheduled"
    elif terminal_status in {
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    }:
        status = terminal_status
    else:
        status = prior.status if prior is not None else "running"
    started_at = prior.started_at if prior is not None else event.occurred_at
    if event.type is ExecutionEventType.WORK_UNIT_REGISTERED:
        started_at = operation_started_at
    completed_at = prior.completed_at if prior is not None else None
    if terminal_status is not None:
        completed_at = event.occurred_at
    failure = prior.failure if prior is not None else None
    if isinstance(event.public_payload, FailurePublicPayload):
        failure = PublicOperationFailureV2(
            code=event.public_payload.code,
            retryable=event.public_payload.retryable,
        )
    retry = prior.retry if prior is not None else None
    if isinstance(event.public_payload, RetryPublicPayload):
        retry = PublicOperationRetryV2(delay_ms=event.public_payload.delay_ms)
    attempt = PublicOperationAttemptV2(
        attempt=event.attempt,
        status=cast(OperationAttemptStatusV2, status),
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=_duration_ms(event, started_at),
        failure=failure,
        retry=retry,
    )
    if index is None:
        attempts.append(attempt)
    else:
        attempts[index] = attempt
    limit = (
        DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.max_attempt_history_per_operation
    )
    return tuple(attempts[-limit:])


def _duration_ms(event: ExecutionEventV2, started_at: str) -> int:
    duration = getattr(event.public_payload, "duration_ms", None)
    if isinstance(duration, int):
        return duration
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    observed = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
    return max(0, int((observed - started).total_seconds() * 1000))


def _apply_stage_event(
    state: ExecutionStageState,
    event: ExecutionEventV2,
) -> ExecutionStageState:
    if state.root_status in _TERMINAL_STAGE_ROOT_STATUSES:
        # Result inventory (including the optional redacted execution log)
        # may be committed after the root terminal event.  Late metadata must
        # not reopen or otherwise mutate the authoritative stage.
        return state
    payload = event.public_payload
    if isinstance(payload, TrackingPublicPayload) or event.type is (
        ExecutionEventType.SEQUENCE_GAP
    ):
        return state
    progress_state = _apply_progress_stage_event(state, event)
    if progress_state is not None:
        return progress_state
    terminal_signal = _TERMINAL_STAGE_SIGNAL_BY_TYPE.get(event.type)
    if terminal_signal in {
        ExecutionStageSignal.ROOT_SUCCEEDED,
        ExecutionStageSignal.ROOT_PARTIAL,
    } and (
        state.stage != ExecutionStage.RESPONSE_SETTLEMENT.value
        or not state.answer_available
    ):
        state = reduce_execution_stage(
            state,
            ExecutionStageSignal.ANSWER_AVAILABLE,
            occurred_at=event.occurred_at,
        )
    signal = terminal_signal or _event_stage_signal(event)
    return _reduce_or_observe_stage(state, event, signal)


def _apply_progress_stage_event(
    state: ExecutionStageState,
    event: ExecutionEventV2,
) -> ExecutionStageState | None:
    """Handle progress observations that do not represent stage changes."""
    payload = event.public_payload
    if not isinstance(payload, ProgressPublicPayload):
        return None
    if payload.observation == "provider_contact":
        return reduce_execution_stage(
            state,
            ExecutionStageSignal.PROVIDER_CONTACT_UNCHANGED,
            occurred_at=event.occurred_at,
        )
    if payload.observation == "liveness":
        return state
    return None


def _event_stage_signal(
    event: ExecutionEventV2,
) -> ExecutionStageSignal | None:
    """Map one non-terminal execution fact to its stage signal."""
    signal: ExecutionStageSignal | None = None
    if event.type in {
        ExecutionEventType.MESSAGE_SNAPSHOT,
        ExecutionEventType.MESSAGE_COMPLETED,
    }:
        signal = ExecutionStageSignal.ANSWER_AVAILABLE
    elif event.type in {
        ExecutionEventType.ARTIFACT_PUBLISHED,
        ExecutionEventType.RESULT_PUBLISHED,
    }:
        signal = ExecutionStageSignal.CONSOLIDATION_STARTED
    elif event.work_unit_id is not None:
        signal = _work_unit_stage_signal(event)
    return signal


def _work_unit_stage_signal(
    event: ExecutionEventV2,
) -> ExecutionStageSignal | None:
    """Map one work-unit observation to the monotonic stage vocabulary."""
    operation_key = _event_operation_key(event)
    provider_state = _provider_state(event)
    signal: ExecutionStageSignal | None = (
        ExecutionStageSignal.SCIENTIFIC_EXECUTION_STARTED
    )
    if operation_key in {"remote.reconcile", "artifact.package"}:
        signal = ExecutionStageSignal.CONSOLIDATION_STARTED
    elif (
        event.type is ExecutionEventType.WORK_UNIT_SUBMITTED
        or provider_state == "submitted"
    ):
        signal = ExecutionStageSignal.PROVIDER_SUBMITTED
    elif provider_state in _ACTIVE_PROVIDER_STATES:
        signal = ExecutionStageSignal.PROVIDER_STATE_CHANGED
    elif operation_key == "remote.submit":
        signal = None
    return signal


def _reduce_or_observe_stage(
    state: ExecutionStageState,
    event: ExecutionEventV2,
    signal: ExecutionStageSignal | None,
) -> ExecutionStageState:
    """Reduce a monotonic signal or record only its execution clock."""
    if signal is None:
        return _observe_stage_fact(state, event.occurred_at)
    target_stage = _STAGE_BY_SIGNAL.get(signal)
    if target_stage is not None and list(ExecutionStage).index(
        target_stage
    ) < list(ExecutionStage).index(ExecutionStage(state.stage)):
        return _observe_stage_fact(state, event.occurred_at)
    return reduce_execution_stage(state, signal, occurred_at=event.occurred_at)


def _observe_stage_fact(
    state: ExecutionStageState,
    occurred_at: str,
) -> ExecutionStageState:
    """Advance only the execution observation clock."""
    return state.model_copy(
        update={"clocks": state.clocks.observe_execution_fact(occurred_at)}
    )


def _event_operation_key(event: ExecutionEventV2) -> str | None:
    payload = event.public_payload
    if isinstance(payload, WorkUnitPublicPayload):
        return payload.operation_key
    if isinstance(payload, PhasePublicPayload):
        return payload.phase
    return None


def _provider_state(event: ExecutionEventV2) -> str | None:
    payload = event.public_payload
    if isinstance(payload, WorkUnitPublicPayload) and payload.detail:
        value = payload.detail.get("provider_state")
        return value if isinstance(value, str) else None
    return None
