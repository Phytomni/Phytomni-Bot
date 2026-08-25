# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared context and durable scheduling boundaries for Runtime V2."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from ..storage.path_policy import IdFactory
from .execution_content_stream_v2 import execution_content_stream_for_db
from .execution_journal_v2 import (
    SpanStatus,
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_runtime_contracts import ExecutionContext, ExecutionServices
from .execution_work_store_v2 import (
    JoinPolicy,
    SpanSpec,
    WorkUnitSpec,
)


@dataclass(frozen=True, slots=True)
class ExecutionBoundary:
    """The active causal context and explicit durable services."""

    context: ExecutionContext
    services: ExecutionServices


@dataclass(frozen=True, slots=True)
class ToolPublicPresentation:
    """Finite public metadata for one shared tool call."""

    operation_key: str
    label_key: str
    started_text: str
    succeeded_text: str
    failed_text: str


@dataclass(frozen=True, slots=True)
class _ToolObservation:
    boundary: ExecutionBoundary
    presentation: ToolPublicPresentation
    work_unit: Any
    span: Any
    started_at: float


@dataclass(frozen=True, slots=True)
class _NestedAgentObservation:
    boundary: ExecutionBoundary
    agent: Any
    span: Any
    phase: str
    started_at: float


@dataclass(slots=True)
class _ModelObservation:
    """One grouped shared-model operation and its current attempt facts."""

    boundary: ExecutionBoundary
    work_unit: Any
    span: Any
    started_at: float
    max_attempts: int
    retry_count: int = 0


_CURRENT_BOUNDARY: ContextVar[ExecutionBoundary | None] = ContextVar(
    "execution_boundary_v2",
    default=None,
)
_CURRENT_MODEL_OBSERVATION: ContextVar[_ModelObservation | None] = ContextVar(
    "model_observation_v2",
    default=None,
)


@contextmanager
def bind_execution_boundary(
    context: ExecutionContext,
    services: ExecutionServices,
) -> Iterator[ExecutionBoundary]:
    """Bind one Runtime boundary across awaited and child-task calls."""
    boundary = ExecutionBoundary(context=context, services=services)
    token = _CURRENT_BOUNDARY.set(boundary)
    try:
        yield boundary
    finally:
        _CURRENT_BOUNDARY.reset(token)


def current_execution_boundary(
    *, required: bool = False
) -> ExecutionBoundary | None:
    """Return the active Runtime boundary or fail closed when required."""
    boundary = _CURRENT_BOUNDARY.get()
    if required and boundary is None:
        raise RuntimeError("execution_boundary_required")
    return boundary


def publish_execution_content_delta(delta: str) -> None:
    """Mirror one public transport delta into transient V2 continuation."""
    if not delta:
        return
    boundary = current_execution_boundary()
    if boundary is None:
        return
    db_path = getattr(boundary.services.journal, "db_path", None)
    if not isinstance(db_path, str) or not db_path:
        return
    projection = boundary.services.journal.get_projection(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
    )
    execution_content_stream_for_db(db_path).publish_next(
        owner=boundary.context.owner_ref,
        execution_id=boundary.context.execution_id,
        output_revision=max(1, projection.output_revision),
        delta=delta,
    )


def advance_execution_todo(phase: str, *, completed: bool) -> None:
    """Advance the catalog-owned Todo plan from one public phase fact."""
    boundary = current_execution_boundary()
    if boundary is None:
        return
    phases = boundary.context.agent.todo_phases
    if phase not in phases:
        return
    phase_index = phases.index(phase)
    active_index = phase_index + (1 if completed else 0)
    statuses = tuple(
        (
            "completed"
            if index < active_index
            else (
                "in_progress"
                if index == active_index and active_index < len(phases)
                else "pending"
            )
        )
        for index in range(len(phases))
    )
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": "todo.snapshot",
                "status": "running",
                "source": "agent",
                "span_id": boundary.context.current_span_id,
                "parent_span_id": boundary.context.parent_span_id,
                "attempt": 1,
                "summary": {
                    "key": "todo.snapshot",
                    "text": "Todo plan updated",
                },
                "public_payload": {
                    "items": [
                        {
                            "id": item,
                            "label_key": f"chat.execution.todoPhase.{item}",
                            "status": status,
                        }
                        for item, status in zip(phases, statuses, strict=True)
                    ]
                },
                "target": {
                    "kind": "todo",
                    "id": f"todo:{boundary.context.agent.slug}",
                },
                "idempotency_key": (
                    f"todo:{boundary.context.agent.slug}:phase:{phase}:"
                    f"{'completed' if completed else 'started'}"
                ),
            }
        ),
    )


async def instrument_tool_invocation[T](
    tool_key: str,
    call: Callable[[], Awaitable[T]],
) -> T:
    """Observe one tool call without exposing its inputs, output, or errors."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    observation = _start_tool_observation(boundary, tool_key)
    if observation is None:
        return await call()
    nested = ExecutionBoundary(
        context=boundary.context.nested(
            agent=boundary.context.agent,
            span_id=observation.span.span_id,
        ),
        services=boundary.services,
    )
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_tool_observation(
            observation,
            status=(
                WorkUnitStatus.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else WorkUnitStatus.FAILED
            ),
        )
        raise
    _finish_tool_observation(observation, status=WorkUnitStatus.SUCCEEDED)
    return result


async def instrument_model_invocation[T](
    call: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 1,
) -> T:
    """Observe one shared Chat/model operation without copying model data."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    observation = _start_model_observation(
        boundary,
        max_attempts=max(1, max_attempts),
    )
    if observation is None:
        return await call()
    nested = ExecutionBoundary(
        context=boundary.context.nested(
            agent=boundary.context.agent,
            span_id=observation.span.span_id,
        ),
        services=boundary.services,
    )
    token = _CURRENT_MODEL_OBSERVATION.set(observation)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_model_observation(
            observation,
            status=(
                WorkUnitStatus.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else WorkUnitStatus.FAILED
            ),
        )
        raise
    finally:
        _CURRENT_MODEL_OBSERVATION.reset(token)
    _finish_model_observation(
        observation,
        status=WorkUnitStatus.SUCCEEDED,
    )
    return result


def record_model_retry(*, delay_ms: int, code: str) -> None:
    """Append one finite retry fact for the active shared model operation."""
    observation = _CURRENT_MODEL_OBSERVATION.get()
    if observation is None:
        return
    observation.retry_count += 1
    next_attempt = min(
        observation.max_attempts,
        observation.work_unit.attempt + observation.retry_count,
    )
    _append_model_fact(
        observation.boundary,
        event_type="work_unit.retry_scheduled",
        status="retry_scheduled",
        span_id=observation.span.span_id,
        parent_span_id=observation.span.parent_span_id,
        work_unit_id=observation.work_unit.work_unit_id,
        attempt=max(1, next_attempt - 1),
        text="Model retry scheduled",
        payload={
            "delay_ms": max(0, delay_ms),
            "next_attempt": next_attempt,
            "code": _safe_model_retry_code(code),
        },
        suffix=f"retry:{next_attempt}",
    )


def record_model_attempt_started(attempt: int) -> None:
    """Append the next retry attempt start under the same logical work unit."""
    observation = _CURRENT_MODEL_OBSERVATION.get()
    if (
        observation is None
        or attempt < 2
        or attempt > observation.max_attempts
    ):
        return
    _append_model_fact(
        observation.boundary,
        event_type="work_unit.attempt_started",
        status="running",
        span_id=observation.span.span_id,
        parent_span_id=observation.span.parent_span_id,
        work_unit_id=observation.work_unit.work_unit_id,
        attempt=attempt,
        text="Model attempt started",
        payload={"operation_key": "model.generate"},
        suffix=f"attempt:{attempt}:started",
    )


def _start_model_observation(
    boundary: ExecutionBoundary,
    *,
    max_attempts: int,
) -> _ModelObservation | None:
    context = boundary.context
    work_unit_id = IdFactory().new_id("work", "model")
    span_id = IdFactory().new_id("span", "model")
    try:
        work_unit = boundary.services.work.create_work_unit(
            WorkUnitSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                work_unit_id=work_unit_id,
                parent_span_id=context.current_span_id,
                operation_key="model.generate",
                driver="model",
                max_attempts=max_attempts,
                deadline_at=(
                    context.deadline_at.isoformat()
                    if context.deadline_at is not None
                    else None
                ),
            )
        )
        _append_model_fact(
            boundary,
            event_type="work_unit.registered",
            status="queued",
            span_id=context.current_span_id,
            parent_span_id=context.parent_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            text="Model work registered",
            payload={"operation_key": "model.generate"},
            suffix="registered",
        )
        span = boundary.services.work.create_span(
            SpanSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                span_id=span_id,
                parent_span_id=context.current_span_id,
                work_unit_id=work_unit_id,
                kind="model_attempt",
                label_key="execution.operation.model.generate",
                attempt=work_unit.attempt,
            )
        )
        _append_model_fact(
            boundary,
            event_type="span.created",
            status="pending",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            text="Model attempt created",
            payload={"phase": "model.generate"},
            suffix="span:created",
        )
        work_unit = boundary.services.work.update_work_unit_status(
            context.execution_id,
            work_unit_id,
            owner=context.owner_ref,
            status=WorkUnitStatus.RUNNING,
            expected_revision=work_unit.revision,
        )
        _append_model_fact(
            boundary,
            event_type="work_unit.attempt_started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            text="Model generation started",
            payload={"operation_key": "model.generate"},
            suffix="attempt:1:started",
        )
        span = boundary.services.work.update_span_status(
            context.execution_id,
            span_id,
            owner=context.owner_ref,
            status=SpanStatus.RUNNING,
            expected_revision=span.revision,
        )
        _append_model_fact(
            boundary,
            event_type="span.started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            text="Model generation started",
            payload={"phase": "model.generate"},
            suffix="span:started",
        )
        return _ModelObservation(
            boundary=boundary,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
            max_attempts=max_attempts,
        )
    except Exception:
        return None


def _finish_model_observation(
    observation: _ModelObservation,
    *,
    status: WorkUnitStatus,
) -> None:
    boundary = observation.boundary
    context = boundary.context
    duration_ms = max(
        0, int((time.perf_counter() - observation.started_at) * 1000)
    )
    if status is WorkUnitStatus.SUCCEEDED:
        work_event, span_event = "work_unit.succeeded", "span.succeeded"
        span_status = SpanStatus.SUCCEEDED
        text = "Model generation completed"
        work_payload: dict[str, object] = {
            "operation_key": "model.generate",
            "duration_ms": duration_ms,
        }
        span_payload: dict[str, object] = {
            "phase": "model.generate",
            "duration_ms": duration_ms,
        }
    elif status is WorkUnitStatus.CANCELLED:
        work_event, span_event = "work_unit.cancelled", "span.cancelled"
        span_status = SpanStatus.CANCELLED
        text = "Model generation cancelled"
        work_payload = {
            "operation_key": "model.generate",
            "duration_ms": duration_ms,
        }
        span_payload = {
            "phase": "model.generate",
            "duration_ms": duration_ms,
        }
    else:
        work_event, span_event = "work_unit.failed", "span.failed"
        span_status = SpanStatus.FAILED
        text = "Model generation failed"
        work_payload = {
            "code": "model_invocation_failed",
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
        _append_model_fact(
            boundary,
            event_type=work_event,
            status=status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            attempt=min(
                observation.max_attempts,
                observation.work_unit.attempt + observation.retry_count,
            ),
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
        _append_model_fact(
            boundary,
            event_type=span_event,
            status=span_status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            attempt=min(
                observation.max_attempts,
                observation.work_unit.attempt + observation.retry_count,
            ),
            text=text,
            payload=span_payload,
            suffix=f"span:{span_status.value}",
        )


def _append_model_fact(
    boundary: ExecutionBoundary,
    *,
    event_type: str,
    status: str,
    span_id: str,
    parent_span_id: str | None,
    work_unit_id: str,
    attempt: int,
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
                "attempt": attempt,
                "summary": {
                    "key": f"model.generate.{suffix}",
                    "text": text,
                },
                "public_payload": payload,
                "idempotency_key": f"work:{work_unit_id}:{suffix}",
            }
        ),
    )


def _safe_model_retry_code(code: str) -> str:
    allowed = {
        "model_transport_retry",
        "model_rate_limited",
        "model_invalid_response",
    }
    return code if code in allowed else "model_transport_retry"


async def instrument_nested_agent_invocation[T](
    agent_slug: str,
    call: Callable[[], Awaitable[T]],
) -> T:
    """Represent an internal public-Agent delegation as a child span."""
    boundary = current_execution_boundary()
    if boundary is None:
        return await call()
    observation = _start_nested_agent_observation(boundary, agent_slug)
    if observation is None:
        return await call()
    nested = ExecutionBoundary(
        context=boundary.context.nested(
            agent=observation.agent,
            span_id=observation.span.span_id,
        ),
        services=boundary.services,
    )
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_nested_agent_observation(
            observation,
            cancelled=isinstance(exc, asyncio.CancelledError),
            succeeded=False,
        )
        raise
    _finish_nested_agent_observation(
        observation,
        cancelled=False,
        succeeded=True,
    )
    return result


def _start_nested_agent_observation(
    boundary: ExecutionBoundary,
    agent_slug: str,
) -> _NestedAgentObservation | None:
    from ..public_agent_catalog import public_agent_spec

    agent = public_agent_spec(agent_slug)
    if agent is None:
        return None
    context = boundary.context
    phase = f"agent.{agent.slug}"
    span_id = IdFactory().new_id("span", "agent")
    try:
        span = boundary.services.work.create_span(
            SpanSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                span_id=span_id,
                parent_span_id=context.current_span_id,
                kind="nested_agent",
                label_key=phase,
            )
        )
        _append_nested_agent_fact(
            boundary,
            event_type="span.created",
            status="pending",
            span=span,
            phase=phase,
            text="Agent delegation created",
            payload={"phase": phase},
        )
        span = boundary.services.work.update_span_status(
            context.execution_id,
            span_id,
            owner=context.owner_ref,
            status=SpanStatus.RUNNING,
            expected_revision=span.revision,
        )
        _append_nested_agent_fact(
            boundary,
            event_type="span.started",
            status="running",
            span=span,
            phase=phase,
            text="Agent delegation started",
            payload={"phase": phase},
        )
        return _NestedAgentObservation(
            boundary=boundary,
            agent=agent,
            span=span,
            phase=phase,
            started_at=time.perf_counter(),
        )
    except Exception:
        return None


def _finish_nested_agent_observation(
    observation: _NestedAgentObservation,
    *,
    cancelled: bool,
    succeeded: bool,
) -> None:
    if succeeded:
        span_status = SpanStatus.SUCCEEDED
        event_type = "span.succeeded"
        text = (
            "Analysis submitted"
            if observation.agent.lifecycle == "asynchronous"
            else "Agent delegation completed"
        )
        payload: dict[str, object] = {"phase": observation.phase}
    elif cancelled:
        span_status = SpanStatus.CANCELLED
        event_type = "span.cancelled"
        text = "Agent delegation cancelled"
        payload = {"phase": observation.phase}
    else:
        span_status = SpanStatus.FAILED
        event_type = "span.failed"
        text = "Agent delegation failed"
        payload = {
            "code": "nested_agent_failed",
            "retryable": False,
        }
    payload["duration_ms"] = max(
        0, int((time.perf_counter() - observation.started_at) * 1000)
    )
    context = observation.boundary.context
    with suppress(Exception):
        span = observation.boundary.services.work.update_span_status(
            context.execution_id,
            observation.span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=observation.span.revision,
        )
        _append_nested_agent_fact(
            observation.boundary,
            event_type=event_type,
            status=span_status.value,
            span=span,
            phase=observation.phase,
            text=text,
            payload=payload,
        )


def _append_nested_agent_fact(
    boundary: ExecutionBoundary,
    *,
    event_type: str,
    status: str,
    span: Any,
    phase: str,
    text: str,
    payload: dict[str, object],
) -> None:
    suffix = event_type.rsplit(".", 1)[-1]
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": status,
                "source": "agent",
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "attempt": span.attempt,
                "summary": {
                    "key": f"{phase}.{suffix}",
                    "text": text,
                },
                "public_payload": payload,
                "idempotency_key": f"span:{span.span_id}:{suffix}",
            }
        ),
    )


def _tool_presentation(tool_key: str) -> ToolPublicPresentation:
    # Imported lazily so the runtime contract does not make the Agent catalog
    # depend on concrete instrumentation during module initialization.
    from ..public_agent_catalog import PUBLIC_AGENT_CATALOG

    spec = next(
        (item for item in PUBLIC_AGENT_CATALOG if item.tool == tool_key), None
    )
    if spec is None:
        return ToolPublicPresentation(
            operation_key="tool.other",
            label_key="tool.other",
            started_text="Running tool",
            succeeded_text="Tool completed",
            failed_text="Tool failed",
        )
    if spec.lifecycle == "asynchronous":
        return ToolPublicPresentation(
            operation_key=f"tool.{spec.slug}",
            label_key=f"tool.{spec.slug}",
            started_text="Submitting analysis",
            succeeded_text="Analysis submitted",
            failed_text="Analysis submission failed",
        )
    display = spec.slug.replace("_", " ").title()
    return ToolPublicPresentation(
        operation_key=f"tool.{spec.slug}",
        label_key=f"tool.{spec.slug}",
        started_text=f"Running {display}",
        succeeded_text=f"{display} completed",
        failed_text=f"{display} failed",
    )


def _start_tool_observation(
    boundary: ExecutionBoundary,
    tool_key: str,
) -> _ToolObservation | None:
    context = boundary.context
    services = boundary.services
    presentation = _tool_presentation(tool_key)
    work_unit_id = IdFactory().new_id("work", "tool")
    span_id = IdFactory().new_id("span", "tool")
    try:
        work_unit = services.work.create_work_unit(
            WorkUnitSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                work_unit_id=work_unit_id,
                parent_span_id=context.current_span_id,
                operation_key=presentation.operation_key,
                driver="tool",
                max_attempts=1,
            )
        )
        _append_tool_fact(
            boundary,
            event_type="work_unit.registered",
            status="queued",
            span_id=context.current_span_id,
            parent_span_id=context.parent_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            presentation=presentation,
            text="Tool work registered",
            payload={"operation_key": presentation.operation_key},
            idempotency_key=f"work:{work_unit_id}:registered",
        )
        span = services.work.create_span(
            SpanSpec(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                span_id=span_id,
                parent_span_id=context.current_span_id,
                work_unit_id=work_unit_id,
                kind="tool_attempt",
                label_key=presentation.label_key,
                attempt=work_unit.attempt,
            )
        )
        _append_tool_fact(
            boundary,
            event_type="span.created",
            status="pending",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            presentation=presentation,
            text="Tool attempt created",
            payload={"phase": presentation.label_key},
            idempotency_key=f"span:{span_id}:created",
        )
        work_unit = services.work.update_work_unit_status(
            context.execution_id,
            work_unit_id,
            owner=context.owner_ref,
            status=WorkUnitStatus.RUNNING,
            expected_revision=work_unit.revision,
        )
        _append_tool_fact(
            boundary,
            event_type="work_unit.attempt_started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            presentation=presentation,
            text=presentation.started_text,
            payload={"operation_key": presentation.operation_key},
            idempotency_key=(
                f"work:{work_unit_id}:attempt:{work_unit.attempt}:started"
            ),
        )
        span = services.work.update_span_status(
            context.execution_id,
            span_id,
            owner=context.owner_ref,
            status=SpanStatus.RUNNING,
            expected_revision=span.revision,
        )
        _append_tool_fact(
            boundary,
            event_type="span.started",
            status="running",
            span_id=span_id,
            parent_span_id=context.current_span_id,
            work_unit_id=work_unit_id,
            attempt=work_unit.attempt,
            presentation=presentation,
            text=presentation.started_text,
            payload={"phase": presentation.label_key},
            idempotency_key=f"span:{span_id}:started",
        )
        return _ToolObservation(
            boundary=boundary,
            presentation=presentation,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
        )
    except Exception:
        return None


def _finish_tool_observation(
    observation: _ToolObservation,
    *,
    status: WorkUnitStatus,
) -> None:
    boundary = observation.boundary
    context = boundary.context
    presentation = observation.presentation
    duration_ms = max(
        0, int((time.perf_counter() - observation.started_at) * 1000)
    )
    if status is WorkUnitStatus.SUCCEEDED:
        work_event, span_event = "work_unit.succeeded", "span.succeeded"
        span_status = SpanStatus.SUCCEEDED
        text = presentation.succeeded_text
        work_payload: dict[str, object] = {
            "operation_key": presentation.operation_key,
            "duration_ms": duration_ms,
        }
        span_payload: dict[str, object] = {
            "phase": presentation.label_key,
            "duration_ms": duration_ms,
        }
    elif status is WorkUnitStatus.CANCELLED:
        work_event, span_event = "work_unit.cancelled", "span.cancelled"
        span_status = SpanStatus.CANCELLED
        text = "Tool cancelled"
        work_payload = {
            "operation_key": presentation.operation_key,
            "duration_ms": duration_ms,
        }
        span_payload = {
            "phase": presentation.label_key,
            "duration_ms": duration_ms,
        }
    else:
        work_event, span_event = "work_unit.failed", "span.failed"
        span_status = SpanStatus.FAILED
        text = presentation.failed_text
        work_payload = {
            "code": "tool_execution_failed",
            "retryable": False,
            "work_unit_id": observation.work_unit.work_unit_id,
            "duration_ms": duration_ms,
        }
        span_payload = {
            "code": "tool_execution_failed",
            "retryable": False,
            "work_unit_id": observation.work_unit.work_unit_id,
            "duration_ms": duration_ms,
        }
    with suppress(Exception):
        boundary.services.work.update_work_unit_status(
            context.execution_id,
            observation.work_unit.work_unit_id,
            owner=context.owner_ref,
            status=status,
            expected_revision=observation.work_unit.revision,
        )
        _append_tool_fact(
            boundary,
            event_type=work_event,
            status=status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            attempt=observation.work_unit.attempt,
            presentation=presentation,
            text=text,
            payload=work_payload,
            idempotency_key=(
                f"work:{observation.work_unit.work_unit_id}:"
                f"attempt:{observation.work_unit.attempt}:{status.value}"
            ),
        )
        boundary.services.work.update_span_status(
            context.execution_id,
            observation.span.span_id,
            owner=context.owner_ref,
            status=span_status,
            expected_revision=observation.span.revision,
        )
        _append_tool_fact(
            boundary,
            event_type=span_event,
            status=span_status.value,
            span_id=observation.span.span_id,
            parent_span_id=observation.span.parent_span_id,
            work_unit_id=observation.work_unit.work_unit_id,
            attempt=observation.work_unit.attempt,
            presentation=presentation,
            text=text,
            payload=span_payload,
            idempotency_key=f"span:{observation.span.span_id}:{span_status.value}",
        )


def _append_tool_fact(
    boundary: ExecutionBoundary,
    *,
    event_type: str,
    status: str,
    span_id: str,
    parent_span_id: str | None,
    work_unit_id: str,
    attempt: int,
    presentation: ToolPublicPresentation,
    text: str,
    payload: dict[str, object],
    idempotency_key: str,
) -> None:
    suffix = event_type.rsplit(".", 1)[-1]
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": status,
                "source": "tool",
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "work_unit_id": work_unit_id,
                "attempt": attempt,
                "summary": {
                    "key": f"{presentation.label_key}.{suffix}",
                    "text": text,
                },
                "public_payload": payload,
                "idempotency_key": idempotency_key,
            }
        ),
    )


def schedule_durable_child_work[T](
    *,
    work_unit_id: str,
    operation_key: str,
    driver: str,
    call: Callable[[], Awaitable[T]],
    join_policy: JoinPolicy | None = None,
    max_attempts: int = 1,
    deadline_at: str | None = None,
) -> asyncio.Task[T]:
    """Persist child-work intent before scheduling its coroutine.

    All durable public-Agent child tasks use this helper.  A process failure
    after registration but before task execution therefore leaves a pending
    work unit for the supervisor instead of losing an untracked task.
    """
    boundary = current_execution_boundary(required=True)
    assert boundary is not None
    context = boundary.context
    services = boundary.services
    create_work_unit = getattr(services.work, "create_work_unit", None)
    if not callable(create_work_unit):
        raise RuntimeError("durable_work_registration_unavailable")
    record = create_work_unit(
        WorkUnitSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            work_unit_id=work_unit_id,
            parent_span_id=context.current_span_id,
            operation_key=operation_key,
            driver=driver,
            join_policy=join_policy,
            max_attempts=max_attempts,
            deadline_at=deadline_at,
        )
    )
    services.journal.append(
        context.execution_id,
        owner=context.owner_ref,
        intent=parse_execution_event_intent_v2(
            {
                "type": "work_unit.registered",
                "status": "queued",
                "source": "runtime",
                "span_id": context.current_span_id,
                "parent_span_id": context.parent_span_id,
                "work_unit_id": work_unit_id,
                "attempt": int(getattr(record, "attempt", 1)),
                "summary": {
                    "key": f"work.{operation_key}.registered",
                    "text": "Work registered",
                },
                "public_payload": {"operation_key": operation_key},
                "idempotency_key": f"work:{work_unit_id}:registered",
            }
        ),
    )

    async def run_child() -> T:
        return await call()

    return cast(asyncio.Task[T], asyncio.create_task(run_child()))


def schedule_public_agent_child_work[T](
    *,
    work_unit_id: str,
    operation_key: str,
    driver: str,
    call: Callable[[], Awaitable[T]],
    join_policy: JoinPolicy | None = None,
    max_attempts: int = 1,
    deadline_at: str | None = None,
) -> asyncio.Task[T]:
    """Use durable V2 scheduling when bound, with a legacy thin fallback.

    Public transports bind the Runtime before Agent business code executes,
    so production V2 work is registered before scheduling. The fallback keeps
    direct unit tests and bounded V1 adapters behavior-compatible without
    copying the scheduling decision into Agent modules.
    """
    if current_execution_boundary() is None:

        async def run_legacy_child() -> T:
            return await call()

        return asyncio.create_task(run_legacy_child())
    return schedule_durable_child_work(
        work_unit_id=work_unit_id,
        operation_key=operation_key,
        driver=driver,
        call=call,
        join_policy=join_policy,
        max_attempts=max_attempts,
        deadline_at=deadline_at,
    )
