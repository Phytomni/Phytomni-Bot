# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared durable instrumentation for external provider operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from ..storage.path_policy import IdFactory
from .execution_instrumentation_v2 import (
    ExecutionBoundary,
    bind_execution_boundary,
    current_execution_boundary,
)
from .execution_journal_v2 import (
    SpanStatus,
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_trace_target_v2 import trace_target_for_operation
from .execution_work_store_v2 import SpanSpec, WorkUnitRecord, WorkUnitSpec
from .operation_instrumentation_v2 import record_provider_contact


@dataclass(slots=True)
class _ProviderAttempt:
    boundary: ExecutionBoundary
    provider_kind: str
    operation_key: str
    analysis_work_unit: WorkUnitRecord
    analysis_span: Any
    submission_work_unit: WorkUnitRecord
    submission_span: Any
    retry_count: int = 0


_CURRENT_PROVIDER_ATTEMPT: ContextVar[_ProviderAttempt | None] = ContextVar(
    "provider_attempt_v2",
    default=None,
)


async def instrument_provider_submission[T](
    *,
    provider_kind: str,
    operation_key: str,
    call: Callable[[], Awaitable[T]],
    identity_from_result: Callable[[T], str],
    call_with_idempotency: Callable[[str], Awaitable[T]] | None = None,
    max_attempts: int = 1,
    require_identity: bool = True,
) -> T:
    """Persist submission intent, then bind the private acknowledgement."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    attempt = _start_provider_attempt(
        boundary,
        provider_kind=provider_kind,
        operation_key=operation_key,
        max_attempts=max_attempts,
    )
    nested = ExecutionBoundary(
        context=boundary.context.nested(
            agent=boundary.context.agent,
            span_id=attempt.submission_span.span_id,
        ),
        services=boundary.services,
    )
    token = _CURRENT_PROVIDER_ATTEMPT.set(attempt)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = (
                await call_with_idempotency(_provider_idempotency_key(attempt))
                if call_with_idempotency is not None
                else await call()
            )
    except BaseException as exc:
        _fail_provider_attempt(
            attempt,
            cancelled=isinstance(exc, asyncio.CancelledError),
        )
        raise
    finally:
        _CURRENT_PROVIDER_ATTEMPT.reset(token)
    provider_task_id = identity_from_result(result)
    if not isinstance(provider_task_id, str) or not provider_task_id.strip():
        _fail_provider_attempt(attempt, cancelled=False)
        if require_identity:
            raise ValueError("provider acknowledgement omitted identity")
        return result
    _acknowledge_provider_attempt(attempt, provider_task_id.strip())
    return result


def record_provider_retry(*, delay_ms: int, code: str) -> None:
    """Record a bounded retry without exposing an upstream response."""
    attempt = _CURRENT_PROVIDER_ATTEMPT.get()
    if attempt is None:
        return
    attempt.retry_count += 1
    next_attempt = attempt.submission_work_unit.attempt + attempt.retry_count
    _append_provider_fact(
        attempt.boundary,
        event_type="work_unit.retry_scheduled",
        status="retry_scheduled",
        span_id=attempt.submission_span.span_id,
        parent_span_id=attempt.submission_span.parent_span_id,
        work_unit_id=attempt.submission_work_unit.work_unit_id,
        attempt=attempt.submission_work_unit.attempt,
        summary_key="remote.submit.retry_scheduled",
        summary_text="Provider retry scheduled",
        payload={
            "delay_ms": max(0, delay_ms),
            "next_attempt": next_attempt,
            "code": _safe_code(code),
        },
        idempotency_key=(
            "work:"
            f"{attempt.submission_work_unit.work_unit_id}:retry:{next_attempt}"
        ),
    )


async def instrument_provider_observation(
    *,
    provider_kind: str,
    provider_task_id: str,
    source_revision: int | None,
    observed_status: str,
) -> bool:
    """Commit one callback/poll revision under its existing work unit."""
    boundary = current_execution_boundary()
    if boundary is None:
        return False
    context = boundary.context
    record = boundary.services.work.find_work_unit_by_provider_task_id(
        context.execution_id,
        provider_task_id,
        owner=context.owner_ref,
        provider_kind=provider_kind,
    )
    if record is None:
        return False
    return record_provider_observation(
        boundary=boundary,
        record=record,
        provider_kind=provider_kind,
        provider_task_id=provider_task_id,
        source_revision=source_revision,
        observed_status=observed_status,
    )


def record_provider_observation(
    *,
    boundary: ExecutionBoundary,
    record: WorkUnitRecord,
    provider_kind: str,
    provider_task_id: str,
    source_revision: int | None,
    observed_status: str,
) -> bool:
    """CAS-fold one provider fact from callback or recovery polling."""
    context = boundary.context
    if (
        record.owner != context.owner_ref
        or record.execution_id != context.execution_id
        or record.provider_kind != provider_kind
        or record.provider_task_id != provider_task_id
    ):
        return False
    analysis_span = boundary.services.work.find_span_by_work_unit_id(
        context.execution_id,
        record.work_unit_id,
        owner=context.owner_ref,
    )
    event_span_id = (
        analysis_span.span_id
        if analysis_span is not None
        else record.parent_span_id
    )
    event_parent_span_id = (
        analysis_span.parent_span_id
        if analysis_span is not None
        else context.parent_span_id
    )
    normalized = observed_status.strip().lower()
    target_status = _work_status_for_observation(normalized)
    terminal = {
        WorkUnitStatus.SUCCEEDED,
        WorkUnitStatus.PARTIAL,
        WorkUnitStatus.FAILED,
        WorkUnitStatus.CANCELLED,
        WorkUnitStatus.TIMED_OUT,
    }
    if record.status in terminal:
        return False
    if source_revision is None:
        if target_status is not None and record.status is target_status:
            record_provider_contact(boundary, record)
            return False
        source_revision = record.provider_revision + 1
    elif source_revision <= record.provider_revision:
        record_provider_contact(boundary, record)
        return False
    event_type, status, payload = _provider_observation_fact(
        normalized,
        record,
        source_revision,
    )
    semantic_changed = (
        target_status is not None and record.status is not target_status
    )
    if source_revision > record.provider_revision:
        record = boundary.services.work.bind_provider(
            context.execution_id,
            record.work_unit_id,
            owner=context.owner_ref,
            provider_kind=provider_kind,
            provider_task_id=provider_task_id,
            provider_revision=source_revision,
            expected_revision=record.revision,
        )
    if target_status is not None and record.status is not target_status:
        record = boundary.services.work.update_work_unit_status(
            context.execution_id,
            record.work_unit_id,
            owner=context.owner_ref,
            status=target_status,
            expected_revision=record.revision,
        )
    if not semantic_changed:
        record_provider_contact(boundary, record)
        return True
    _append_provider_fact(
        boundary,
        event_type=event_type,
        status=status,
        span_id=event_span_id,
        parent_span_id=event_parent_span_id,
        work_unit_id=record.work_unit_id,
        attempt=record.attempt,
        summary_key=f"remote.reconcile.{normalized or 'unknown'}",
        summary_text=_provider_observation_summary(normalized),
        payload=payload,
        idempotency_key=(
            f"provider:{record.work_unit_id}:revision:{source_revision}:"
            f"{normalized or 'unknown'}"
        ),
    )
    if (
        analysis_span is not None
        and target_status is not None
        and target_status in terminal
    ):
        span_status = SpanStatus(target_status.value)
        boundary.services.work.update_span_status(
            context.execution_id,
            analysis_span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=analysis_span.revision,
        )
        _append_provider_fact(
            boundary,
            event_type=f"span.{span_status.value}",
            status=span_status.value,
            span_id=analysis_span.span_id,
            parent_span_id=analysis_span.parent_span_id,
            work_unit_id=record.work_unit_id,
            attempt=record.attempt,
            summary_key=(f"{record.operation_key}.{span_status.value}"),
            summary_text=_provider_observation_summary(normalized),
            payload={"phase": record.operation_key},
            idempotency_key=(
                f"span:{analysis_span.span_id}:{span_status.value}"
            ),
        )
    return True


async def instrument_provider_cancellation[T](
    *,
    provider_kind: str,
    provider_task_id: str,
    call: Callable[[], Awaitable[T]],
    outcome_from_result: (
        Callable[[T], Literal["confirmed", "best_effort", "unsupported"]]
        | None
    ) = None,
) -> T:
    """Record finite cancellation semantics around the provider call."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    context = boundary.context
    record = boundary.services.work.find_work_unit_by_provider_task_id(
        context.execution_id,
        provider_task_id,
        owner=context.owner_ref,
        provider_kind=provider_kind,
    )
    if record is None:
        return await call()
    analysis_span = boundary.services.work.find_span_by_work_unit_id(
        context.execution_id,
        record.work_unit_id,
        owner=context.owner_ref,
    )
    record = boundary.services.work.set_cancellation_state(
        context.execution_id,
        record.work_unit_id,
        owner=context.owner_ref,
        state="requested",
        expected_revision=record.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.cancellation_requested",
        status="cancellation_requested",
        span_id=record.parent_span_id,
        parent_span_id=context.parent_span_id,
        work_unit_id=record.work_unit_id,
        attempt=record.attempt,
        summary_key="provider.cancellation.requested",
        summary_text="Provider cancellation requested",
        payload={"outcome": "requested"},
        idempotency_key=f"work:{record.work_unit_id}:cancellation:requested",
    )
    result = await call()
    outcome = (
        outcome_from_result(result)
        if outcome_from_result is not None
        else "confirmed"
    )
    if outcome not in {"confirmed", "best_effort", "unsupported"}:
        raise ValueError("unsupported provider cancellation outcome")
    record = boundary.services.work.set_cancellation_state(
        context.execution_id,
        record.work_unit_id,
        owner=context.owner_ref,
        state=outcome,
        expected_revision=record.revision,
    )
    if outcome == "confirmed":
        record = boundary.services.work.update_work_unit_status(
            context.execution_id,
            record.work_unit_id,
            owner=context.owner_ref,
            status=WorkUnitStatus.CANCELLED,
            expected_revision=record.revision,
        )
    _append_provider_fact(
        boundary,
        event_type=(
            "work_unit.cancellation_confirmed"
            if outcome == "confirmed"
            else "work_unit.cancellation_requested"
        ),
        status=(
            "cancelled" if outcome == "confirmed" else "cancellation_requested"
        ),
        span_id=record.parent_span_id,
        parent_span_id=context.parent_span_id,
        work_unit_id=record.work_unit_id,
        attempt=record.attempt,
        summary_key=f"provider.cancellation.{outcome}",
        summary_text=(
            "Provider cancellation confirmed"
            if outcome == "confirmed"
            else "Provider cancellation state updated"
        ),
        payload={"outcome": outcome},
        idempotency_key=f"work:{record.work_unit_id}:cancellation:{outcome}",
    )
    if outcome == "confirmed" and analysis_span is not None:
        boundary.services.work.update_span_status(
            context.execution_id,
            analysis_span.span_id,
            owner=context.owner_ref,
            status=SpanStatus.CANCELLED,
            expected_revision=analysis_span.revision,
        )
        _append_provider_fact(
            boundary,
            event_type="span.cancelled",
            status="cancelled",
            span_id=analysis_span.span_id,
            parent_span_id=analysis_span.parent_span_id,
            work_unit_id=record.work_unit_id,
            attempt=record.attempt,
            summary_key=f"{record.operation_key}.cancelled",
            summary_text="External analysis was cancelled",
            payload={"phase": record.operation_key},
            idempotency_key=f"span:{analysis_span.span_id}:cancelled",
        )
    return result


def _start_provider_attempt(
    boundary: ExecutionBoundary,
    *,
    provider_kind: str,
    operation_key: str,
    max_attempts: int,
) -> _ProviderAttempt:
    context = boundary.context
    analysis_work_unit_id = IdFactory().new_id("work", "provider")
    analysis_span_id = IdFactory().new_id("span", "provider")
    submission_work_unit_id = IdFactory().new_id("work", "submit")
    submission_span_id = IdFactory().new_id("span", "submit")
    analysis_work = boundary.services.work.create_work_unit(
        WorkUnitSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            work_unit_id=analysis_work_unit_id,
            parent_span_id=context.current_span_id,
            operation_key=operation_key,
            driver="provider",
            max_attempts=max(1, max_attempts),
            deadline_at=(
                context.deadline_at.isoformat()
                if context.deadline_at is not None
                else None
            ),
        )
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.registered",
        status="queued",
        span_id=context.current_span_id,
        parent_span_id=context.parent_span_id,
        work_unit_id=analysis_work_unit_id,
        attempt=analysis_work.attempt,
        summary_key=f"{operation_key}.registered",
        summary_text="Provider work registered",
        payload={"operation_key": operation_key},
        idempotency_key=f"work:{analysis_work_unit_id}:registered",
        target=trace_target_for_operation(
            agent_slug=context.agent.slug,
            operation_key=operation_key,
            execution_id=context.execution_id,
            work_unit_id=analysis_work_unit_id,
        ),
    )
    analysis_span = boundary.services.work.create_span(
        SpanSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            span_id=analysis_span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=analysis_work_unit_id,
            kind="provider_work",
            label_key=operation_key,
            attempt=analysis_work.attempt,
        )
    )
    _append_provider_fact(
        boundary,
        event_type="span.created",
        status="pending",
        span_id=analysis_span_id,
        parent_span_id=context.current_span_id,
        work_unit_id=analysis_work_unit_id,
        attempt=analysis_work.attempt,
        summary_key=f"{operation_key}.created",
        summary_text="Provider analysis created",
        payload={"phase": operation_key},
        idempotency_key=f"span:{analysis_span_id}:created",
    )
    analysis_work = boundary.services.work.update_work_unit_status(
        context.execution_id,
        analysis_work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.DISPATCHING,
        expected_revision=analysis_work.revision,
    )
    analysis_span = boundary.services.work.update_span_status(
        context.execution_id,
        analysis_span_id,
        owner=context.owner_ref,
        status=SpanStatus.RUNNING,
        expected_revision=analysis_span.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="span.started",
        status="running",
        span_id=analysis_span_id,
        parent_span_id=context.current_span_id,
        work_unit_id=analysis_work_unit_id,
        attempt=analysis_work.attempt,
        summary_key=f"{operation_key}.started",
        summary_text="Provider analysis started",
        payload={"phase": operation_key},
        idempotency_key=f"span:{analysis_span_id}:started",
    )

    submission_work = boundary.services.work.create_work_unit(
        WorkUnitSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            work_unit_id=submission_work_unit_id,
            parent_span_id=analysis_span_id,
            operation_key="remote.submit",
            driver="provider_submission",
            max_attempts=max(1, max_attempts),
            deadline_at=(
                context.deadline_at.isoformat()
                if context.deadline_at is not None
                else None
            ),
        )
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.registered",
        status="queued",
        span_id=analysis_span_id,
        parent_span_id=context.current_span_id,
        work_unit_id=submission_work_unit_id,
        attempt=submission_work.attempt,
        summary_key="remote.submit.registered",
        summary_text="Provider submission registered",
        payload={"operation_key": "remote.submit"},
        idempotency_key=f"work:{submission_work_unit_id}:registered",
    )
    submission_span = boundary.services.work.create_span(
        SpanSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            span_id=submission_span_id,
            parent_span_id=analysis_span_id,
            work_unit_id=submission_work_unit_id,
            kind="provider_attempt",
            label_key="remote.submit",
            attempt=submission_work.attempt,
        )
    )
    _append_provider_fact(
        boundary,
        event_type="span.created",
        status="pending",
        span_id=submission_span_id,
        parent_span_id=analysis_span_id,
        work_unit_id=submission_work_unit_id,
        attempt=submission_work.attempt,
        summary_key="remote.submit.created",
        summary_text="Provider submission created",
        payload={"phase": "remote.submit"},
        idempotency_key=f"span:{submission_span_id}:created",
    )
    submission_work = boundary.services.work.update_work_unit_status(
        context.execution_id,
        submission_work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.DISPATCHING,
        expected_revision=submission_work.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.attempt_started",
        status="dispatching",
        span_id=submission_span_id,
        parent_span_id=analysis_span_id,
        work_unit_id=submission_work_unit_id,
        attempt=submission_work.attempt,
        summary_key="remote.submit.attempt_started",
        summary_text="Provider submission running",
        payload={"operation_key": "remote.submit"},
        idempotency_key=(f"work:{submission_work_unit_id}:attempt:1:started"),
    )
    submission_span = boundary.services.work.update_span_status(
        context.execution_id,
        submission_span_id,
        owner=context.owner_ref,
        status=SpanStatus.RUNNING,
        expected_revision=submission_span.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="span.started",
        status="running",
        span_id=submission_span_id,
        parent_span_id=analysis_span_id,
        work_unit_id=submission_work_unit_id,
        attempt=submission_work.attempt,
        summary_key="remote.submit.started",
        summary_text="Provider submission running",
        payload={"phase": "remote.submit"},
        idempotency_key=f"span:{submission_span_id}:started",
    )
    return _ProviderAttempt(
        boundary=boundary,
        provider_kind=provider_kind,
        operation_key=operation_key,
        analysis_work_unit=analysis_work,
        analysis_span=analysis_span,
        submission_work_unit=submission_work,
        submission_span=submission_span,
    )


def _acknowledge_provider_attempt(
    attempt: _ProviderAttempt,
    provider_task_id: str,
) -> None:
    boundary = attempt.boundary
    context = boundary.context
    submission = boundary.services.work.update_work_unit_status(
        context.execution_id,
        attempt.submission_work_unit.work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.SUBMITTED,
        expected_revision=attempt.submission_work_unit.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.submitted",
        status="submitted",
        span_id=attempt.submission_span.span_id,
        parent_span_id=attempt.submission_span.parent_span_id,
        work_unit_id=submission.work_unit_id,
        attempt=submission.attempt,
        summary_key="remote.submit.submitted",
        summary_text="Provider submission sent",
        payload={"operation_key": "remote.submit"},
        idempotency_key=f"work:{submission.work_unit_id}:submitted",
    )
    submission = boundary.services.work.update_work_unit_status(
        context.execution_id,
        submission.work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.ACKNOWLEDGED,
        expected_revision=submission.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.acknowledged",
        status="acknowledged",
        span_id=attempt.submission_span.span_id,
        parent_span_id=attempt.submission_span.parent_span_id,
        work_unit_id=submission.work_unit_id,
        attempt=submission.attempt,
        summary_key="remote.submit.acknowledged",
        summary_text="Provider acknowledged submission",
        payload={"operation_key": "remote.submit", "source_revision": 0},
        idempotency_key=f"work:{submission.work_unit_id}:acknowledged",
    )
    submission = boundary.services.work.update_work_unit_status(
        context.execution_id,
        submission.work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.SUCCEEDED,
        expected_revision=submission.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.succeeded",
        status="succeeded",
        span_id=attempt.submission_span.span_id,
        parent_span_id=attempt.submission_span.parent_span_id,
        work_unit_id=submission.work_unit_id,
        attempt=submission.attempt,
        summary_key="remote.submit.succeeded",
        summary_text="Provider submission acknowledged",
        payload={"operation_key": "remote.submit"},
        idempotency_key=f"work:{submission.work_unit_id}:succeeded",
    )
    boundary.services.work.update_span_status(
        context.execution_id,
        attempt.submission_span.span_id,
        owner=context.owner_ref,
        status=SpanStatus.SUCCEEDED,
        expected_revision=attempt.submission_span.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="span.succeeded",
        status="succeeded",
        span_id=attempt.submission_span.span_id,
        parent_span_id=attempt.submission_span.parent_span_id,
        work_unit_id=submission.work_unit_id,
        attempt=submission.attempt,
        summary_key="remote.submit.succeeded",
        summary_text="Provider submission acknowledged",
        payload={"phase": "remote.submit"},
        idempotency_key=(f"span:{attempt.submission_span.span_id}:succeeded"),
    )

    analysis = boundary.services.work.update_work_unit_status(
        context.execution_id,
        attempt.analysis_work_unit.work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.SUBMITTED,
        expected_revision=attempt.analysis_work_unit.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.submitted",
        status="submitted",
        span_id=attempt.analysis_span.span_id,
        parent_span_id=attempt.analysis_span.parent_span_id,
        work_unit_id=analysis.work_unit_id,
        attempt=analysis.attempt,
        summary_key=f"{attempt.operation_key}.submitted",
        summary_text="Provider analysis submitted",
        payload={"operation_key": attempt.operation_key},
        idempotency_key=f"work:{analysis.work_unit_id}:submitted",
    )
    analysis = boundary.services.work.bind_provider(
        context.execution_id,
        analysis.work_unit_id,
        owner=context.owner_ref,
        provider_kind=attempt.provider_kind,
        provider_task_id=provider_task_id,
        provider_revision=0,
        expected_revision=analysis.revision,
    )
    analysis = boundary.services.work.update_work_unit_status(
        context.execution_id,
        analysis.work_unit_id,
        owner=context.owner_ref,
        status=WorkUnitStatus.ACKNOWLEDGED,
        expected_revision=analysis.revision,
    )
    _append_provider_fact(
        boundary,
        event_type="work_unit.acknowledged",
        status="acknowledged",
        span_id=attempt.analysis_span.span_id,
        parent_span_id=attempt.analysis_span.parent_span_id,
        work_unit_id=analysis.work_unit_id,
        attempt=analysis.attempt,
        summary_key=f"{attempt.operation_key}.acknowledged",
        summary_text="Provider acknowledged analysis",
        payload={"operation_key": attempt.operation_key, "source_revision": 0},
        idempotency_key=f"work:{analysis.work_unit_id}:acknowledged",
    )
    attempt.submission_work_unit = submission
    attempt.analysis_work_unit = analysis


def _provider_idempotency_key(attempt: _ProviderAttempt) -> str:
    """Return an opaque key recoverable from the durable logical work id."""
    return (
        f"phyto:{attempt.boundary.context.execution_id}:"
        f"{attempt.analysis_work_unit.work_unit_id}:"
        f"{attempt.analysis_work_unit.attempt}"
    )


def _fail_provider_attempt(
    attempt: _ProviderAttempt,
    *,
    cancelled: bool,
) -> None:
    status = WorkUnitStatus.CANCELLED if cancelled else WorkUnitStatus.FAILED
    span_status = SpanStatus.CANCELLED if cancelled else SpanStatus.FAILED
    work_event = "work_unit.cancelled" if cancelled else "work_unit.failed"
    span_event = "span.cancelled" if cancelled else "span.failed"
    work_payload: dict[str, object]
    span_payload: dict[str, object]
    if cancelled:
        work_payload = {"operation_key": attempt.operation_key}
        span_payload = {"phase": attempt.operation_key}
    else:
        work_payload = {
            "code": "provider_submission_failed",
            "retryable": False,
            "work_unit_id": attempt.submission_work_unit.work_unit_id,
        }
        span_payload = dict(work_payload)
    context = attempt.boundary.context
    with suppress(Exception):
        attempt.boundary.services.work.update_work_unit_status(
            context.execution_id,
            attempt.submission_work_unit.work_unit_id,
            owner=context.owner_ref,
            status=status,
            expected_revision=attempt.submission_work_unit.revision,
        )
        _append_provider_fact(
            attempt.boundary,
            event_type=work_event,
            status=status.value,
            span_id=attempt.submission_span.span_id,
            parent_span_id=attempt.submission_span.parent_span_id,
            work_unit_id=attempt.submission_work_unit.work_unit_id,
            attempt=attempt.submission_work_unit.attempt,
            summary_key=f"remote.submit.{status.value}",
            summary_text="Provider submission did not complete",
            payload=work_payload,
            idempotency_key=(
                f"work:{attempt.submission_work_unit.work_unit_id}:"
                f"{status.value}"
            ),
        )
        attempt.boundary.services.work.update_span_status(
            context.execution_id,
            attempt.submission_span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=attempt.submission_span.revision,
        )
        _append_provider_fact(
            attempt.boundary,
            event_type=span_event,
            status=span_status.value,
            span_id=attempt.submission_span.span_id,
            parent_span_id=attempt.submission_span.parent_span_id,
            work_unit_id=attempt.submission_work_unit.work_unit_id,
            attempt=attempt.submission_work_unit.attempt,
            summary_key=f"remote.submit.{span_status.value}",
            summary_text="Provider submission did not complete",
            payload=span_payload,
            idempotency_key=(
                f"span:{attempt.submission_span.span_id}:"
                f"{span_status.value}"
            ),
        )
        attempt.boundary.services.work.update_work_unit_status(
            context.execution_id,
            attempt.analysis_work_unit.work_unit_id,
            owner=context.owner_ref,
            status=status,
            expected_revision=attempt.analysis_work_unit.revision,
        )
        _append_provider_fact(
            attempt.boundary,
            event_type=work_event,
            status=status.value,
            span_id=attempt.analysis_span.span_id,
            parent_span_id=attempt.analysis_span.parent_span_id,
            work_unit_id=attempt.analysis_work_unit.work_unit_id,
            attempt=attempt.analysis_work_unit.attempt,
            summary_key=f"{attempt.operation_key}.{status.value}",
            summary_text="Provider analysis did not start",
            payload={"operation_key": attempt.operation_key},
            idempotency_key=(
                f"work:{attempt.analysis_work_unit.work_unit_id}:"
                f"{status.value}"
            ),
        )
        attempt.boundary.services.work.update_span_status(
            context.execution_id,
            attempt.analysis_span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=attempt.analysis_span.revision,
        )
        _append_provider_fact(
            attempt.boundary,
            event_type=span_event,
            status=span_status.value,
            span_id=attempt.analysis_span.span_id,
            parent_span_id=attempt.analysis_span.parent_span_id,
            work_unit_id=attempt.analysis_work_unit.work_unit_id,
            attempt=attempt.analysis_work_unit.attempt,
            summary_key=f"{attempt.operation_key}.{span_status.value}",
            summary_text="Provider analysis did not start",
            payload={"phase": attempt.operation_key},
            idempotency_key=(
                f"span:{attempt.analysis_span.span_id}:" f"{span_status.value}"
            ),
        )


def _provider_observation_fact(
    status: str,
    record: WorkUnitRecord,
    source_revision: int,
) -> tuple[str, str, dict[str, object]]:
    if status == "succeeded":
        return (
            "work_unit.succeeded",
            "succeeded",
            {
                "operation_key": record.operation_key,
                "source_revision": source_revision,
            },
        )
    if status == "cancelled":
        return (
            "work_unit.cancelled",
            "cancelled",
            {
                "operation_key": record.operation_key,
                "source_revision": source_revision,
            },
        )
    if status in {"failed", "timed_out"}:
        return (
            (
                "work_unit.failed"
                if status == "failed"
                else "work_unit.timed_out"
            ),
            status,
            {
                "code": f"provider_{status}",
                "retryable": False,
                "work_unit_id": record.work_unit_id,
            },
        )
    return (
        "work_unit.acknowledged",
        "running",
        {
            "operation_key": record.operation_key,
            "source_revision": source_revision,
        },
    )


def _provider_observation_summary(status: str) -> str:
    return {
        "pending": "External analysis is queued",
        "running": "External analysis is running",
        "succeeded": "External analysis completed",
        "failed": "External analysis failed",
        "cancelled": "External analysis was cancelled",
        "timed_out": "External analysis timed out",
    }.get(status, "External analysis status updated")


def _work_status_for_observation(status: str) -> WorkUnitStatus | None:
    return {
        "pending": WorkUnitStatus.ACKNOWLEDGED,
        "running": WorkUnitStatus.RUNNING,
        "succeeded": WorkUnitStatus.SUCCEEDED,
        "failed": WorkUnitStatus.FAILED,
        "cancelled": WorkUnitStatus.CANCELLED,
        "timed_out": WorkUnitStatus.TIMED_OUT,
    }.get(status)


def _safe_code(code: str) -> str:
    allowed = "".join(
        character
        for character in code.lower()
        if character.isalnum() or character in {"_", "-"}
    )
    return allowed[:128] or "provider_retry"


def _append_provider_fact(
    boundary: ExecutionBoundary,
    *,
    event_type: str,
    status: str,
    span_id: str,
    parent_span_id: str | None,
    work_unit_id: str,
    attempt: int,
    summary_key: str,
    summary_text: str,
    payload: dict[str, object],
    idempotency_key: str,
    target: dict[str, str] | None = None,
) -> None:
    raw: dict[str, object] = {
        "type": event_type,
        "status": status,
        "source": "provider",
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "work_unit_id": work_unit_id,
        "attempt": attempt,
        "summary": {"key": summary_key, "text": summary_text},
        "public_payload": payload,
        "idempotency_key": idempotency_key,
    }
    if target is not None:
        raw["target"] = target
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(raw),
    )
