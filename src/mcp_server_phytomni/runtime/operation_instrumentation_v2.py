# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fail-closed work-unit instrumentation for finite operations."""

from __future__ import annotations

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
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_observation_store_v2 import (
    failed_observation_payloads,
    failed_work_status,
    finish_work_observation,
    observation_deadline,
    observation_duration_ms,
    start_work_observation,
    terminal_observation_presentation,
)
from .execution_trace_detail import (
    OPERATION_PRESENTER_REGISTRY,
    PresentedOperation,
)
from .execution_work_status_v2 import TERMINAL_WORK_UNIT_STATUSES
from .execution_work_store_v2 import WorkUnitRecord


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
    """Record an operation without copying its input, result, or error."""
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
    nested = boundary.nested_for_span(observation.span.span_id)
    token = _CURRENT_OPERATION.set(observation)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_operation(
            observation,
            status=failed_work_status(exc),
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
    """Append a coalesced observation for the active operation."""
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
    if record.status in TERMINAL_WORK_UNIT_STATUSES:
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
    work_unit_id = IdFactory().new_id("work", "operation")
    span_id = IdFactory().new_id("span", "operation")
    observation = None
    with suppress(Exception):
        payload: dict[str, object] = {"operation_key": presenter.operation_key}
        if presenter.detail:
            payload["detail"] = presenter.detail
        work_unit, span = start_work_observation(
            boundary,
            {
                "work_unit_id": work_unit_id,
                "operation_key": presenter.operation_key,
                "driver": "operation",
                "max_attempts": 1,
                "deadline_at": observation_deadline(boundary),
            },
            {
                "span_id": span_id,
                "work_unit_id": work_unit_id,
                "kind": "operation_attempt",
                "label_key": presenter.label_key,
                "attempt": 1,
            },
            {
                "source": "runtime",
                "summary_prefix": presenter.operation_key,
                "registered_text": f"{presenter.fallback_label} registered",
                "created_text": f"{presenter.fallback_label} created",
                "started_text": f"{presenter.fallback_label} started",
                "work_payload": payload,
                "span_payload": {"phase": presenter.operation_key},
                "style": "runtime",
            },
        )
        observation = _OperationObservation(
            boundary=boundary,
            presenter=presenter,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
        )
    return observation


def _failure_code(operation_key: str) -> str:
    return {
        "knowledge.search": "knowledge_search_failed",
        "data.query": "data_query_failed",
        "remote.reconcile": "remote_reconcile_failed",
        "artifact.package": "artifact_package_failed",
    }.get(operation_key, "operation_failed")


def _finish_operation(
    observation: _OperationObservation,
    *,
    status: WorkUnitStatus,
    detail: Mapping[str, int | bool | str],
) -> None:
    boundary = observation.boundary
    presenter = observation.presenter
    duration_ms = observation_duration_ms(observation.started_at)
    if status is WorkUnitStatus.SUCCEEDED:
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
        text = f"{presenter.fallback_label} failed"
        work_payload, span_payload = failed_observation_payloads(
            _failure_code(presenter.operation_key),
            observation.work_unit.work_unit_id,
            duration_ms,
        )
    terminal = terminal_observation_presentation(
        "runtime",
        presenter.operation_key,
        text,
        (work_payload, span_payload),
        1,
    )
    records = (observation.work_unit, observation.span)
    with suppress(Exception):
        finish_work_observation(boundary, records, status, terminal)
