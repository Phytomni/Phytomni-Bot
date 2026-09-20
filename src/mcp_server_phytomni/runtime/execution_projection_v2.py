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

    status = projection.status
    terminal = projection.terminal
    active_span_ids = projection.active_span_ids
    todo_declared = projection.todo_declared
    todos = projection.todos
    results = projection.results
    targets = projection.targets
    output_revision = projection.output_revision
    output_offset = projection.output_offset
    tracking_health = projection.tracking_health
    failed_work_unit_ids = projection.failed_work_unit_ids
    warnings = projection.warnings
    input_required = projection.input_required
    operation_revision = projection.operation_revision
    operations = _apply_operation_event(projection.operations, event)
    execution_stage = _apply_stage_event(projection.execution_stage, event)

    if terminal is None:
        status = _EXECUTION_STATUS_BY_TYPE.get(event.type, status)
        if event.type is ExecutionEventType.INPUT_REQUIRED:
            status = ExecutionStatus.WAITING_INPUT
            if isinstance(event.public_payload, InputRequiredPublicPayload):
                input_required = event.public_payload
                operation_revision = max(
                    operation_revision,
                    event.public_payload.action_revision,
                )
        elif event.type is ExecutionEventType.INPUT_RESOLVED:
            status = ExecutionStatus.RUNNING
            input_required = None

        if event.type in _SPAN_START_TYPES:
            active_span_ids = _append_unique(active_span_ids, event.span_id)
        elif event.type in _SPAN_END_TYPES:
            active_span_ids = tuple(
                span_id
                for span_id in active_span_ids
                if span_id != event.span_id
            )

        terminal_status = _TERMINAL_STATUS_BY_TYPE.get(event.type)
        if terminal_status is not None:
            status = terminal_status
            active_span_ids = ()
            terminal = TerminalOutcomeV2(
                status=cast(TerminalStatusV2, terminal_status.value),
                event_id=event.event_id,
                result_revision=output_revision,
            )

    if isinstance(event.public_payload, TodoSnapshotPublicPayload):
        todo_declared = True
        todos = event.public_payload.items

    if (
        isinstance(event.public_payload, ResourcePublishedPublicPayload)
        and event.target is not None
    ):
        targets = _append_target(targets, event.target)
        if event.target.kind is not PublicTargetKind.TRACE and all(
            result.id != event.event_id for result in results
        ):
            results = (
                *results,
                PublicResultItemV2(
                    id=event.event_id,
                    name=event.public_payload.name,
                    media_type=event.public_payload.media_type,
                    size_bytes=event.public_payload.size_bytes,
                    target=event.target,
                ),
            )
    elif event.target is not None:
        targets = _append_target(targets, event.target)

    if isinstance(event.public_payload, MessagePublicPayload):
        incoming = (
            event.public_payload.output_revision,
            event.public_payload.offset,
        )
        current = (output_revision, output_offset)
        if incoming >= current:
            output_revision, output_offset = incoming

    if isinstance(event.public_payload, TrackingPublicPayload):
        tracking_health = event.public_payload.health

    if isinstance(event.public_payload, FailurePublicPayload):
        work_unit_id = event.public_payload.work_unit_id or event.work_unit_id
        if work_unit_id is not None:
            failed_work_unit_ids = _append_unique(
                failed_work_unit_ids, work_unit_id
            )
        warning = PublicWarningV2(
            code=event.public_payload.code,
            work_unit_id=work_unit_id,
        )
        if warning not in warnings:
            warnings = (*warnings, warning)

    if terminal is not None and terminal.result_revision < output_revision:
        terminal = terminal.model_copy(
            update={"result_revision": output_revision}
        )

    return ExecutionProjectionV2(
        execution_id=projection.execution_id,
        agent_slug=projection.agent_slug,
        status=status,
        latest_seq=event.seq,
        output_revision=output_revision,
        output_offset=output_offset,
        operation_revision=operation_revision,
        operations=operations,
        execution_stage=execution_stage,
        tracking_health=tracking_health,
        active_span_ids=active_span_ids,
        todo_declared=todo_declared,
        todos=todos,
        results=results,
        targets=targets,
        failed_work_unit_ids=failed_work_unit_ids,
        warnings=warnings,
        input_required=input_required,
        context_stage=projection.context_stage,
        terminal=terminal,
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
    existing_index = next(
        (
            index
            for index, operation in enumerate(operations)
            if operation.work_unit_id == work_unit_id
        ),
        None,
    )
    existing = (
        operations[existing_index] if existing_index is not None else None
    )
    operation_key = _operation_key(event, existing)
    presenter = OPERATION_PRESENTER_REGISTRY.resolve(operation_key)
    operation_key = presenter.operation_key
    status = _operation_status(event, existing)
    completed_at = (
        event.occurred_at
        if event.type in _OPERATION_TERMINAL_STATUS
        else existing.completed_at if existing is not None else None
    )
    started_at = existing.started_at if existing else event.occurred_at
    duration_ms = _duration_ms(event, started_at)
    if existing is not None and event.type not in _OPERATION_TERMINAL_STATUS:
        duration_ms = max(existing.duration_ms, duration_ms)
    attempts = _apply_attempt(existing, event, started_at)
    detail = existing.detail if existing is not None else {}
    if (
        isinstance(event.public_payload, WorkUnitPublicPayload)
        and event.public_payload.detail is not None
    ):
        detail = event.public_payload.detail
    progress = existing.progress if existing is not None else None
    if (
        isinstance(event.public_payload, ProgressPublicPayload)
        and event.public_payload.completed is not None
        and event.public_payload.total is not None
    ):
        unit = event.public_payload.unit
        if unit is None and len(presenter.counter_units) == 1:
            unit = presenter.counter_units[0]
        if unit is not None:
            progress = PublicOperationProgressV2(
                completed=event.public_payload.completed,
                total=event.public_payload.total,
                unit=unit,
            )
    summary = existing.summary if existing is not None else None
    if event.type in _OPERATION_TERMINAL_STATUS or event.type is (
        ExecutionEventType.WORK_UNIT_RETRY_SCHEDULED
    ):
        summary = PublicOperationSummaryV2(
            kind="operation", text=event.summary.text
        )
    target = event.target or (
        existing.target if existing is not None else None
    )
    operation = PublicOperationRecordV2(
        operation_id=(
            existing.operation_id
            if existing is not None
            else "operation-"
            + hashlib.sha256(work_unit_id.encode("utf-8")).hexdigest()[:32]
        ),
        work_unit_id=work_unit_id,
        operation_key=operation_key,
        label_key=presenter.label_key,
        fallback_label=presenter.fallback_label,
        status=status,
        started_at=started_at,
        last_observation_at=event.occurred_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        current_attempt=max(
            event.attempt,
            existing.current_attempt if existing is not None else 1,
        ),
        attempts=attempts,
        progress=progress,
        detail=detail,
        summary=summary,
        target=target,
    )
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
    if state.root_status in {
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "timed_out",
    }:
        # Result inventory (including the optional redacted execution log)
        # may be committed after the root terminal event.  Late metadata must
        # not reopen or otherwise mutate the authoritative stage.
        return state
    payload = event.public_payload
    if isinstance(payload, TrackingPublicPayload) or event.type is (
        ExecutionEventType.SEQUENCE_GAP
    ):
        return state
    if isinstance(payload, ProgressPublicPayload):
        if payload.observation == "provider_contact":
            return reduce_execution_stage(
                state,
                ExecutionStageSignal.PROVIDER_CONTACT_UNCHANGED,
                occurred_at=event.occurred_at,
            )
        if payload.observation == "liveness":
            return state

    signal: ExecutionStageSignal | None = None
    operation_key = _event_operation_key(event)
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
        if operation_key in {"remote.reconcile", "artifact.package"}:
            signal = ExecutionStageSignal.CONSOLIDATION_STARTED
        elif (
            event.type is ExecutionEventType.WORK_UNIT_SUBMITTED
            or _provider_state(event) == "submitted"
        ):
            signal = ExecutionStageSignal.PROVIDER_SUBMITTED
        elif _provider_state(event) in {
            "queued",
            "running",
            "consolidating",
            "terminal",
        }:
            signal = ExecutionStageSignal.PROVIDER_STATE_CHANGED
        elif operation_key == "remote.submit":
            signal = None
        else:
            signal = ExecutionStageSignal.SCIENTIFIC_EXECUTION_STARTED

    events = ExecutionEventType
    signals = ExecutionStageSignal
    terminal_signal = {
        events.EXECUTION_SUCCEEDED: signals.ROOT_SUCCEEDED,
        events.EXECUTION_PARTIAL: signals.ROOT_PARTIAL,
        events.EXECUTION_FAILED: signals.ROOT_FAILED,
        events.EXECUTION_CANCELLED: signals.ROOT_CANCELLED,
        events.EXECUTION_TIMED_OUT: signals.ROOT_TIMED_OUT,
    }.get(event.type)
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
    if terminal_signal is not None:
        signal = terminal_signal
    if signal is not None:
        stages = ExecutionStage
        target_stage = {
            signals.PROVIDER_SUBMITTED: stages.ORCHESTRATION,
            signals.SCIENTIFIC_EXECUTION_STARTED: stages.SCIENTIFIC_EXECUTION,
            signals.CONSOLIDATION_STARTED: stages.CONSOLIDATION,
            signals.ANSWER_AVAILABLE: stages.RESPONSE_SETTLEMENT,
        }.get(signal)
        if target_stage is not None and list(ExecutionStage).index(
            target_stage
        ) < list(ExecutionStage).index(ExecutionStage(state.stage)):
            return state.model_copy(
                update={
                    "clocks": state.clocks.observe_execution_fact(
                        event.occurred_at
                    )
                }
            )
        return reduce_execution_stage(
            state, signal, occurred_at=event.occurred_at
        )
    return state.model_copy(
        update={
            "clocks": state.clocks.observe_execution_fact(event.occurred_at)
        }
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
