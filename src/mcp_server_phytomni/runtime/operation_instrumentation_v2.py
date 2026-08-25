# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fail-closed work-unit instrumentation for finite operations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..storage.path_policy import IdFactory
from .execution_event_limits import DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS
from .execution_instrumentation_v2 import (
    ExecutionBoundary,
    bind_execution_boundary,
    current_execution_boundary,
)
from .execution_journal_v2 import (
    ExecutionEventType,
    SpanStatus,
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_trace_detail import (
    OPERATION_PRESENTER_REGISTRY,
    PresentedOperation,
)
from .execution_work_store_v2 import SpanSpec, WorkUnitRecord, WorkUnitSpec


@dataclass(frozen=True, slots=True)
class _OperationObservation:
    boundary: ExecutionBoundary
    presenter: PresentedOperation
    work_unit: Any
    span: Any
    started_at: float


_CURRENT_OPERATION: ContextVar[_OperationObservation | None] = ContextVar(
    "operation_observation_v2",
    default=None,
)


async def instrument_operation_invocation[T](
    operation_key: str,
    call: Callable[[], Awaitable[T]],
    *,
    detail: Mapping[str, Any] | None = None,
    detail_from_result: Callable[[T], Mapping[str, Any]] | None = None,
) -> T:
    """Record one finite operation without copying its input, result, or error."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    presenter = OPERATION_PRESENTER_REGISTRY.present(
        operation_key,
        detail=detail,
    )
    observation = _start_operation(boundary, presenter)
    if observation is None:
        return await call()
    nested = ExecutionBoundary(
        context=boundary.context.nested(
            agent=boundary.context.agent,
            span_id=observation.span.span_id,
        ),
        services=boundary.services,
    )
    token = _CURRENT_OPERATION.set(observation)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_operation(
            observation,
            status=(
                WorkUnitStatus.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else WorkUnitStatus.FAILED
            ),
            detail=presenter.detail,
        )
        raise
    finally:
        _CURRENT_OPERATION.reset(token)
    terminal_detail = dict(presenter.detail)
    if detail_from_result is not None:
        with suppress(Exception):
            terminal_detail.update(detail_from_result(result))
    terminal = OPERATION_PRESENTER_REGISTRY.present(
        presenter.operation_key,
        detail=terminal_detail,
    )
    _finish_operation(
        observation,
        status=WorkUnitStatus.SUCCEEDED,
        detail=terminal.detail,
    )
    return result


def record_current_operation_liveness(
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Append a coalesced no-percentage observation for the active operation."""
    observation = _CURRENT_OPERATION.get()
    if observation is None:
        return False
    return _record_work_unit_observation(
        observation.boundary,
        observation.work_unit,
        observation="liveness",
        observed_at=observed_at,
    )


def record_provider_contact(
    boundary: ExecutionBoundary,
    record: WorkUnitRecord,
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Persist a coalesced provider-contact clock without semantic progress."""
    return _record_work_unit_observation(
        boundary,
        record,
        observation="provider_contact",
        observed_at=observed_at,
    )


def _record_work_unit_observation(
    boundary: ExecutionBoundary,
    record: WorkUnitRecord,
    *,
    observation: str,
    observed_at: datetime | None,
) -> bool:
    if record.status in {
        WorkUnitStatus.SUCCEEDED,
        WorkUnitStatus.PARTIAL,
        WorkUnitStatus.FAILED,
        WorkUnitStatus.CANCELLED,
        WorkUnitStatus.TIMED_OUT,
    }:
        return False
    now = observed_at or boundary.services.clock()
    if observation == "provider_contact":
        return boundary.services.work.observe_provider_contact(
            boundary.context.execution_id,
            record.work_unit_id,
            owner=boundary.context.owner_ref,
            observed_at=now.isoformat(),
        )
    latest_seq = boundary.services.journal.get_projection(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
    ).latest_seq
    page = boundary.services.journal.list_events(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        after_seq=max(0, latest_seq - 200),
        limit=200,
    )
    if page is not None:
        previous = next(
            (
                event
                for event in reversed(page.items)
                if event.work_unit_id == record.work_unit_id
                and event.type is ExecutionEventType.WORK_UNIT_PROGRESS
                and getattr(event.public_payload, "observation", None)
                == observation
            ),
            None,
        )
        if previous is not None:
            previous_at = datetime.fromisoformat(
                previous.occurred_at.replace("Z", "+00:00")
            )
            if DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.should_coalesce_liveness(
                int(previous_at.timestamp() * 1000),
                int(now.timestamp() * 1000),
            ):
                return False
    created_at = datetime.fromisoformat(
        record.created_at.replace("Z", "+00:00")
    )
    elapsed_ms = max(0, int((now - created_at).total_seconds() * 1000))
    presenter = OPERATION_PRESENTER_REGISTRY.resolve(record.operation_key)
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": "work_unit.progress",
                "status": "running",
                "source": (
                    "provider"
                    if observation == "provider_contact"
                    else "runtime"
                ),
                "span_id": record.parent_span_id,
                "parent_span_id": boundary.context.parent_span_id,
                "work_unit_id": record.work_unit_id,
                "attempt": record.attempt,
                "summary": {
                    "key": f"{presenter.operation_key}.{observation}",
                    "text": f"{presenter.fallback_label} is active",
                },
                "public_payload": {
                    "phase": presenter.operation_key,
                    "observation": observation,
                    "elapsed_ms": elapsed_ms,
                },
                "idempotency_key": (
                    f"work:{record.work_unit_id}:{observation}:"
                    f"{int(now.timestamp() * 1000)}"
                ),
            }
        ),
    )
    return True


def _start_operation(
    boundary: ExecutionBoundary,
    presenter: PresentedOperation,
) -> _OperationObservation | None:
    context = boundary.context
    work_unit_id = IdFactory().new_id("work", "operation")
    span_id = IdFactory().new_id("span", "operation")
    try:
        work_unit = boundary.services.work.create_work_unit(
            WorkUnitSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                work_unit_id=work_unit_id,
                parent_span_id=context.current_span_id,
                operation_key=presenter.operation_key,
                driver="operation",
                max_attempts=1,
                deadline_at=(
                    context.deadline_at.isoformat()
                    if context.deadline_at is not None
                    else None
                ),
            )
        )
        payload: dict[str, object] = {"operation_key": presenter.operation_key}
        if presenter.detail:
            payload["detail"] = presenter.detail
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type="work_unit.registered",
            status="queued",
            span_id=context.current_span_id,
            parent_span_id=context.parent_span_id,
            work_unit_id=work_unit_id,
            text=f"{presenter.fallback_label} registered",
            payload=payload,
            suffix="registered",
        )
        span = boundary.services.work.create_span(
            SpanSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                span_id=span_id,
                parent_span_id=context.current_span_id,
                work_unit_id=work_unit_id,
                kind="operation_attempt",
                label_key=presenter.label_key,
                attempt=1,
            )
        )
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type="span.created",
            status="pending",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            text=f"{presenter.fallback_label} created",
            payload={"phase": presenter.operation_key},
            suffix="span:created",
        )
        work_unit = boundary.services.work.update_work_unit_status(
            context.execution_id,
            work_unit_id,
            owner=context.owner_ref,
            status=WorkUnitStatus.RUNNING,
            expected_revision=work_unit.revision,
        )
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type="work_unit.attempt_started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            text=f"{presenter.fallback_label} started",
            payload=payload,
            suffix="attempt:1:started",
        )
        span = boundary.services.work.update_span_status(
            context.execution_id,
            span_id,
            owner=context.owner_ref,
            status=SpanStatus.RUNNING,
            expected_revision=span.revision,
        )
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type="span.started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            text=f"{presenter.fallback_label} started",
            payload={"phase": presenter.operation_key},
            suffix="span:started",
        )
        return _OperationObservation(
            boundary=boundary,
            presenter=presenter,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
        )
    except Exception:
        return None


def _finish_operation(
    observation: _OperationObservation,
    *,
    status: WorkUnitStatus,
    detail: Mapping[str, int | bool | str],
) -> None:
    boundary = observation.boundary
    context = boundary.context
    presenter = observation.presenter
    duration_ms = max(
        0, int((time.perf_counter() - observation.started_at) * 1000)
    )
    if status is WorkUnitStatus.SUCCEEDED:
        work_event, span_event = "work_unit.succeeded", "span.succeeded"
        span_status = SpanStatus.SUCCEEDED
        text = f"{presenter.fallback_label} completed"
        work_payload: dict[str, object] = {
            "operation_key": presenter.operation_key,
            "duration_ms": duration_ms,
        }
        if detail:
            work_payload["detail"] = dict(detail)
        span_payload: dict[str, object] = {
            "phase": presenter.operation_key,
            "duration_ms": duration_ms,
        }
    elif status is WorkUnitStatus.CANCELLED:
        work_event, span_event = "work_unit.cancelled", "span.cancelled"
        span_status = SpanStatus.CANCELLED
        text = f"{presenter.fallback_label} cancelled"
        work_payload = {
            "operation_key": presenter.operation_key,
            "duration_ms": duration_ms,
        }
        span_payload = {
            "phase": presenter.operation_key,
            "duration_ms": duration_ms,
        }
    else:
        work_event, span_event = "work_unit.failed", "span.failed"
        span_status = SpanStatus.FAILED
        text = f"{presenter.fallback_label} failed"
        work_payload = {
            "code": _failure_code(presenter.operation_key),
            "retryable": False,
            "work_unit_id": observation.work_unit.work_unit_id,
            "duration_ms": duration_ms,
        }
        span_payload = dict(work_payload)
    with suppress(Exception):
        boundary.services.work.update_work_unit_status(
            context.execution_id,
            observation.work_unit.work_unit_id,
            owner=context.owner_ref,
            status=status,
            expected_revision=observation.work_unit.revision,
        )
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type=work_event,
            status=status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            text=text,
            payload=work_payload,
            suffix=f"work:{status.value}",
        )
        boundary.services.work.update_span_status(
            context.execution_id,
            observation.span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=observation.span.revision,
        )
        _append_operation_fact(
            boundary,
            presenter=presenter,
            event_type=span_event,
            status=span_status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            text=text,
            payload=span_payload,
            suffix=f"span:{span_status.value}",
        )


def _append_operation_fact(
    boundary: ExecutionBoundary,
    *,
    presenter: PresentedOperation,
    event_type: str,
    status: str,
    span_id: str,
    parent_span_id: str | None,
    work_unit_id: str,
    text: str,
    payload: dict[str, object],
    suffix: str,
) -> None:
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": status,
                "source": "runtime",
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "work_unit_id": work_unit_id,
                "attempt": 1,
                "summary": {
                    "key": f"{presenter.operation_key}.{suffix}",
                    "text": text,
                },
                "public_payload": payload,
                "idempotency_key": f"work:{work_unit_id}:{suffix}",
            }
        ),
    )


def _failure_code(operation_key: str) -> str:
    return {
        "knowledge.search": "knowledge_search_failed",
        "data.query": "data_query_failed",
        "remote.reconcile": "remote_reconcile_failed",
        "artifact.package": "artifact_package_failed",
    }.get(operation_key, "operation_failed")
