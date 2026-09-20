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
from importlib import import_module
from typing import Any, NotRequired, TypedDict, Unpack, cast

from ..storage.path_policy import IdFactory
from .execution_content_stream_v2 import execution_content_stream_for_db
from .execution_journal_v2 import (
    SpanStatus,
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_observation_store_v2 import (
    append_observation_fact,
    create_observed_span,
    failed_observation_payloads,
    failed_work_status,
    finish_work_observation,
    observation_deadline,
    observation_duration_ms,
    start_work_observation,
    terminal_observation_presentation,
    transition_span,
)
from .execution_runtime_contracts import ExecutionContext, ExecutionServices
from .execution_work_store_v2 import (
    JoinPolicy,
    WorkUnitSpec,
)
from .instrumentation_contracts_v2 import (
    ObservationFactIdentity,
    keyword_signature,
)


@dataclass(frozen=True, slots=True)
class ExecutionBoundary:
    """The active causal context and explicit durable services."""

    context: ExecutionContext
    services: ExecutionServices

    def nested(self, *, agent: Any, span_id: str) -> ExecutionBoundary:
        """Create a child boundary while retaining the explicit services."""
        return ExecutionBoundary(
            context=self.context.nested(agent=agent, span_id=span_id),
            services=self.services,
        )

    def nested_for_span(self, span_id: str) -> ExecutionBoundary:
        """Create a same-Agent child boundary for an observed span."""
        return self.nested(agent=self.context.agent, span_id=span_id)


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


class _ModelFactKwargs(ObservationFactIdentity):
    """Presented fields for one model observation fact."""

    text: str
    payload: dict[str, object]
    suffix: str


class _NestedAgentFactKwargs(TypedDict):
    """Presented fields for one nested-Agent span fact."""

    event_type: str
    status: str
    span: Any
    phase: str
    text: str
    payload: dict[str, object]


class ChildWorkOptions(TypedDict):
    """Optional policy accepted by the durable child-work schedulers."""

    join_policy: NotRequired[JoinPolicy | None]
    max_attempts: NotRequired[int]
    deadline_at: NotRequired[str | None]


_SCHEDULE_CHILD_SIGNATURE = keyword_signature(
    (
        ("work_unit_id", "str"),
        ("operation_key", "str"),
        ("driver", "str"),
        ("call", "Callable[[], Awaitable[T]]"),
    ),
    (
        ("join_policy", "JoinPolicy | None", None),
        ("max_attempts", "int", 1),
        ("deadline_at", "str | None", None),
    ),
    return_annotation="asyncio.Task[T]",
)


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
    nested = boundary.nested_for_span(observation.span.span_id)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_tool_observation(
            observation,
            status=failed_work_status(exc),
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
    nested = boundary.nested_for_span(observation.span.span_id)
    token = _CURRENT_MODEL_OBSERVATION.set(observation)
    try:
        with bind_execution_boundary(nested.context, nested.services):
            result = await call()
    except BaseException as exc:
        _finish_model_observation(
            observation,
            status=failed_work_status(exc),
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
    work_unit_id = IdFactory().new_id("work", "model")
    span_id = IdFactory().new_id("span", "model")
    observation = None
    with suppress(Exception):
        work_unit, span = start_work_observation(
            boundary,
            {
                "work_unit_id": work_unit_id,
                "operation_key": "model.generate",
                "driver": "model",
                "max_attempts": max_attempts,
                "deadline_at": observation_deadline(boundary),
            },
            {
                "span_id": span_id,
                "work_unit_id": work_unit_id,
                "kind": "model_attempt",
                "label_key": "execution.operation.model.generate",
            },
            {
                "source": "runtime",
                "summary_prefix": "model.generate",
                "registered_text": "Model work registered",
                "created_text": "Model attempt created",
                "started_text": "Model generation started",
                "work_payload": {"operation_key": "model.generate"},
                "span_payload": {"phase": "model.generate"},
                "style": "runtime",
            },
        )
        observation = _ModelObservation(
            boundary=boundary,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
            max_attempts=max_attempts,
        )
    return observation


def _finish_model_observation(
    observation: _ModelObservation,
    *,
    status: WorkUnitStatus,
) -> None:
    boundary = observation.boundary
    duration_ms = observation_duration_ms(observation.started_at)
    if status is WorkUnitStatus.SUCCEEDED:
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
        text = "Model generation failed"
        work_payload, span_payload = failed_observation_payloads(
            "model_invocation_failed",
            observation.work_unit.work_unit_id,
            duration_ms,
        )
    terminal = terminal_observation_presentation(
        "runtime",
        "model.generate",
        text,
        (work_payload, span_payload),
        min(
            observation.max_attempts,
            observation.work_unit.attempt + observation.retry_count,
        ),
    )
    records = (observation.work_unit, observation.span)
    with suppress(Exception):
        finish_work_observation(boundary, records, status, terminal)


def _append_model_fact(
    boundary: ExecutionBoundary,
    **fact: Unpack[_ModelFactKwargs],
) -> None:
    append_observation_fact(
        boundary,
        {
            "event_type": fact["event_type"],
            "status": fact["status"],
            "source": "runtime",
            "span_id": fact["span_id"],
            "parent_span_id": fact["parent_span_id"],
            "work_unit_id": fact["work_unit_id"],
            "attempt": fact["attempt"],
            "summary_key": f"model.generate.{fact['suffix']}",
            "text": fact["text"],
            "public_payload": fact["payload"],
            "idempotency_key": (
                f"work:{fact['work_unit_id']}:{fact['suffix']}"
            ),
        },
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
    nested = boundary.nested(
        agent=observation.agent,
        span_id=observation.span.span_id,
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
    catalog = import_module("..public_agent_catalog", __package__)
    agent = catalog.public_agent_spec(agent_slug)
    if agent is None:
        return None
    phase = f"agent.{agent.slug}"
    span_id = IdFactory().new_id("span", "agent")
    observation = None
    with suppress(Exception):
        span = create_observed_span(
            boundary,
            {
                "span_id": span_id,
                "kind": "nested_agent",
                "label_key": phase,
            },
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
        span = transition_span(boundary, span, SpanStatus.RUNNING)
        _append_nested_agent_fact(
            boundary,
            event_type="span.started",
            status="running",
            span=span,
            phase=phase,
            text="Agent delegation started",
            payload={"phase": phase},
        )
        observation = _NestedAgentObservation(
            boundary=boundary,
            agent=agent,
            span=span,
            phase=phase,
            started_at=time.perf_counter(),
        )
    return observation


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
    payload["duration_ms"] = observation_duration_ms(observation.started_at)
    with suppress(Exception):
        span = transition_span(
            observation.boundary, observation.span, span_status
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
    **fact: Unpack[_NestedAgentFactKwargs],
) -> None:
    span = fact["span"]
    suffix = fact["event_type"].rsplit(".", 1)[-1]
    append_observation_fact(
        boundary,
        {
            "event_type": fact["event_type"],
            "status": fact["status"],
            "source": "agent",
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "attempt": span.attempt,
            "summary_key": f"{fact['phase']}.{suffix}",
            "text": fact["text"],
            "public_payload": fact["payload"],
            "idempotency_key": f"span:{span.span_id}:{suffix}",
        },
    )


def _start_tool_observation(
    boundary: ExecutionBoundary,
    tool_key: str,
) -> _ToolObservation | None:
    presentation = _tool_presentation(tool_key)
    work_unit_id = IdFactory().new_id("work", "tool")
    span_id = IdFactory().new_id("span", "tool")
    observation = None
    with suppress(Exception):
        work_unit, span = start_work_observation(
            boundary,
            {
                "work_unit_id": work_unit_id,
                "operation_key": presentation.operation_key,
                "driver": "tool",
                "max_attempts": 1,
            },
            {
                "span_id": span_id,
                "work_unit_id": work_unit_id,
                "kind": "tool_attempt",
                "label_key": presentation.label_key,
            },
            {
                "source": "tool",
                "summary_prefix": presentation.label_key,
                "registered_text": "Tool work registered",
                "created_text": "Tool attempt created",
                "started_text": presentation.started_text,
                "work_payload": {"operation_key": presentation.operation_key},
                "span_payload": {"phase": presentation.label_key},
                "style": "tool",
            },
        )
        observation = _ToolObservation(
            boundary=boundary,
            presentation=presentation,
            work_unit=work_unit,
            span=span,
            started_at=time.perf_counter(),
        )
    return observation


def _tool_presentation(tool_key: str) -> ToolPublicPresentation:
    # Imported lazily so the runtime contract does not make the Agent catalog
    # depend on concrete instrumentation during module initialization.
    catalog = import_module("..public_agent_catalog", __package__)
    spec = next(
        (
            item
            for item in catalog.PUBLIC_AGENT_CATALOG
            if item.tool == tool_key
        ),
        None,
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


def _finish_tool_observation(
    observation: _ToolObservation,
    *,
    status: WorkUnitStatus,
) -> None:
    boundary = observation.boundary
    presentation = observation.presentation
    duration_ms = observation_duration_ms(observation.started_at)
    if status is WorkUnitStatus.SUCCEEDED:
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
        text = presentation.failed_text
        work_payload, span_payload = failed_observation_payloads(
            "tool_execution_failed",
            observation.work_unit.work_unit_id,
            duration_ms,
        )
    terminal = terminal_observation_presentation(
        "tool",
        presentation.label_key,
        text,
        (work_payload, span_payload),
        observation.work_unit.attempt,
    )
    records = (observation.work_unit, observation.span)
    with suppress(Exception):
        finish_work_observation(boundary, records, status, terminal)


def schedule_durable_child_work[T](
    *,
    work_unit_id: str,
    operation_key: str,
    driver: str,
    call: Callable[[], Awaitable[T]],
    **options: Unpack[ChildWorkOptions],
) -> asyncio.Task[T]:
    """Persist child-work intent before scheduling its coroutine.

    All durable public-Agent child tasks use this helper.  A process failure
    after registration but before task execution therefore leaves a pending
    work unit for the supervisor instead of losing an untracked task.
    """
    _SCHEDULE_CHILD_SIGNATURE.bind(
        work_unit_id=work_unit_id,
        operation_key=operation_key,
        driver=driver,
        call=call,
        **options,
    )
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
            join_policy=options.get("join_policy"),
            max_attempts=options.get("max_attempts", 1),
            deadline_at=options.get("deadline_at"),
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
    **options: Unpack[ChildWorkOptions],
) -> asyncio.Task[T]:
    """Use durable V2 scheduling when bound, with a legacy thin fallback.

    Public transports bind the Runtime before Agent business code executes,
    so production V2 work is registered before scheduling. The fallback keeps
    direct unit tests and bounded V1 adapters behavior-compatible without
    copying the scheduling decision into Agent modules.
    """
    _SCHEDULE_CHILD_SIGNATURE.bind(
        work_unit_id=work_unit_id,
        operation_key=operation_key,
        driver=driver,
        call=call,
        **options,
    )
    if current_execution_boundary() is None:

        async def run_legacy_child() -> T:
            return await call()

        return asyncio.create_task(run_legacy_child())
    return schedule_durable_child_work(
        work_unit_id=work_unit_id,
        operation_key=operation_key,
        driver=driver,
        call=call,
        **options,
    )


for _scheduler in (
    schedule_durable_child_work,
    schedule_public_agent_child_work,
):
    setattr(_scheduler, "__signature__", _SCHEDULE_CHILD_SIGNATURE)
