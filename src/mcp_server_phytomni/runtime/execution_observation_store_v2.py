# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small persistence primitives shared by Runtime observation boundaries."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from .execution_journal_v2 import (
    SpanStatus,
    WorkUnitStatus,
    parse_execution_event_intent_v2,
)
from .execution_work_store_v2 import (
    JoinPolicy,
    SpanRecord,
    SpanSpec,
    WorkUnitRecord,
    WorkUnitSpec,
)
from .instrumentation_contracts_v2 import ObservationFactIdentity

if TYPE_CHECKING:
    from .execution_instrumentation_v2 import ExecutionBoundary


class WorkUnitIdentity(TypedDict):
    """Caller-owned fields for one observed work unit."""

    work_unit_id: str
    operation_key: str
    driver: str
    max_attempts: int
    deadline_at: NotRequired[str | None]
    join_policy: NotRequired[JoinPolicy | None]


class SpanIdentity(TypedDict):
    """Caller-owned fields for one observed span."""

    span_id: str
    kind: str
    label_key: str
    work_unit_id: NotRequired[str | None]
    attempt: NotRequired[int]
    join_policy: NotRequired[JoinPolicy | None]


class ObservationFact(TypedDict):
    """Finite public fact written by one observation boundary."""

    event_type: str
    status: str
    source: str
    span_id: str
    parent_span_id: str | None
    attempt: int
    summary_key: str
    text: str
    public_payload: dict[str, object]
    work_unit_id: NotRequired[str]
    idempotency_key: NotRequired[str]


class SpanObservationFact(TypedDict):
    """Presented fields for a fact attached to an existing span."""

    event_type: str
    status: str
    source: str
    summary_key: str
    text: str
    public_payload: dict[str, object]
    idempotency_key: NotRequired[str]


FactStyle = Literal["runtime", "tool"]


class StartObservationPresentation(TypedDict):
    """Public presentation for the fixed observation start sequence."""

    source: str
    summary_prefix: str
    registered_text: str
    created_text: str
    started_text: str
    work_payload: dict[str, object]
    span_payload: dict[str, object]
    style: FactStyle


class TerminalObservationPresentation(TypedDict):
    """Public presentation for the fixed observation terminal sequence."""

    source: str
    summary_prefix: str
    text: str
    work_payload: dict[str, object]
    span_payload: dict[str, object]
    attempt: int
    style: FactStyle


class _StartFactValues(ObservationFactIdentity):
    text: str
    payload: dict[str, object]


class _TerminalFactValues(TypedDict):
    event_type: str
    status: str
    span: SpanRecord
    work_unit: WorkUnitRecord
    payload: dict[str, object]


def terminal_observation_presentation(
    style: FactStyle,
    summary_prefix: str,
    text: str,
    payloads: tuple[dict[str, object], dict[str, object]],
    attempt: int,
) -> TerminalObservationPresentation:
    """Build the presentation for one common terminal sequence."""
    work_payload, span_payload = payloads
    return {
        "source": style,
        "summary_prefix": summary_prefix,
        "text": text,
        "work_payload": work_payload,
        "span_payload": span_payload,
        "attempt": attempt,
        "style": style,
    }


def failed_work_status(error: BaseException) -> WorkUnitStatus:
    """Map invocation cancellation and failure to finite work status."""
    if isinstance(error, asyncio.CancelledError):
        return WorkUnitStatus.CANCELLED
    return WorkUnitStatus.FAILED


def observation_duration_ms(started_at: float) -> int:
    """Return a non-negative elapsed duration for public observation facts."""
    return max(0, int((time.perf_counter() - started_at) * 1000))


def observation_deadline(boundary: ExecutionBoundary) -> str | None:
    """Return the active deadline in its durable representation."""
    deadline = boundary.context.deadline_at
    return deadline.isoformat() if deadline is not None else None


def failed_observation_payloads(
    code: str,
    work_unit_id: str,
    duration_ms: int,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build detached bounded payloads for a failed finite observation."""
    work_payload: dict[str, object] = {
        "code": code,
        "retryable": False,
        "work_unit_id": work_unit_id,
        "duration_ms": duration_ms,
    }
    return work_payload, dict(work_payload)


def create_observed_work_unit(
    boundary: ExecutionBoundary,
    identity: WorkUnitIdentity,
) -> WorkUnitRecord:
    """Create one work unit under the active causal span."""
    context = boundary.context
    return boundary.services.work.create_work_unit(
        WorkUnitSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            parent_span_id=context.current_span_id,
            **identity,
        )
    )


def create_observed_span(
    boundary: ExecutionBoundary,
    identity: SpanIdentity,
) -> SpanRecord:
    """Create one span under the active causal span."""
    context = boundary.context
    return boundary.services.work.create_span(
        SpanSpec(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            parent_span_id=context.current_span_id,
            **identity,
        )
    )


def transition_work_unit(
    boundary: ExecutionBoundary,
    record: WorkUnitRecord,
    status: WorkUnitStatus,
) -> WorkUnitRecord:
    """Apply one CAS-protected work-unit transition."""
    return boundary.services.work.update_work_unit_status(
        boundary.context.execution_id,
        record.work_unit_id,
        owner=boundary.context.owner_ref,
        status=status,
        expected_revision=record.revision,
    )


def transition_span(
    boundary: ExecutionBoundary,
    record: SpanRecord,
    status: SpanStatus,
) -> SpanRecord:
    """Apply one CAS-protected span transition."""
    return boundary.services.work.update_span_status(
        boundary.context.execution_id,
        record.span_id,
        owner=boundary.context.owner_ref,
        status=status,
        expected_revision=record.revision,
    )


def append_observation_fact(
    boundary: ExecutionBoundary,
    fact: ObservationFact,
) -> None:
    """Append one already-presented observation fact to the journal."""
    intent: dict[str, object] = {
        "type": fact["event_type"],
        "status": fact["status"],
        "source": fact["source"],
        "span_id": fact["span_id"],
        "parent_span_id": fact["parent_span_id"],
        "attempt": fact["attempt"],
        "summary": {
            "key": fact["summary_key"],
            "text": fact["text"],
        },
        "public_payload": fact["public_payload"],
    }
    if work_unit_id := fact.get("work_unit_id"):
        intent["work_unit_id"] = work_unit_id
    if idempotency_key := fact.get("idempotency_key"):
        intent["idempotency_key"] = idempotency_key
    boundary.services.journal.append(
        boundary.context.execution_id,
        owner=boundary.context.owner_ref,
        intent=parse_execution_event_intent_v2(intent),
    )


def append_span_observation_fact(
    boundary: ExecutionBoundary,
    span: SpanRecord,
    fact: SpanObservationFact,
) -> None:
    """Append a presented fact using identity from an existing span."""
    observation: ObservationFact = {
        "event_type": fact["event_type"],
        "status": fact["status"],
        "source": fact["source"],
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "attempt": span.attempt,
        "summary_key": fact["summary_key"],
        "text": fact["text"],
        "public_payload": fact["public_payload"],
    }
    if idempotency_key := fact.get("idempotency_key"):
        observation["idempotency_key"] = idempotency_key
    append_observation_fact(boundary, observation)


def start_work_observation(
    boundary: ExecutionBoundary,
    work_identity: WorkUnitIdentity,
    span_identity: SpanIdentity,
    presentation: StartObservationPresentation,
) -> tuple[WorkUnitRecord, SpanRecord]:
    """Persist the common registered-created-started observation sequence."""
    context = boundary.context
    work_unit = create_observed_work_unit(boundary, work_identity)
    resolved_span: SpanIdentity = {
        **span_identity,
        "attempt": span_identity.get("attempt", work_unit.attempt),
    }
    span_id = resolved_span["span_id"]
    work_unit_id = work_identity["work_unit_id"]
    attempt = resolved_span["attempt"]
    append_observation_fact(
        boundary,
        _start_fact(
            presentation,
            {
                "event_type": "work_unit.registered",
                "status": "queued",
                "span_id": context.current_span_id,
                "parent_span_id": context.parent_span_id,
                "work_unit_id": work_unit_id,
                "attempt": attempt,
                "text": presentation["registered_text"],
                "payload": presentation["work_payload"],
            },
        ),
    )
    span = create_observed_span(boundary, resolved_span)
    append_observation_fact(
        boundary,
        _start_fact(
            presentation,
            {
                "event_type": "span.created",
                "status": "pending",
                "span_id": span_id,
                "parent_span_id": context.current_span_id,
                "work_unit_id": work_unit_id,
                "attempt": attempt,
                "text": presentation["created_text"],
                "payload": presentation["span_payload"],
            },
        ),
    )
    work_unit = transition_work_unit(
        boundary, work_unit, WorkUnitStatus.RUNNING
    )
    append_observation_fact(
        boundary,
        _start_fact(
            presentation,
            {
                "event_type": "work_unit.attempt_started",
                "status": "running",
                "span_id": span_id,
                "parent_span_id": context.current_span_id,
                "work_unit_id": work_unit_id,
                "attempt": attempt,
                "text": presentation["started_text"],
                "payload": presentation["work_payload"],
            },
        ),
    )
    span = transition_span(boundary, span, SpanStatus.RUNNING)
    append_observation_fact(
        boundary,
        _start_fact(
            presentation,
            {
                "event_type": "span.started",
                "status": "running",
                "span_id": span_id,
                "parent_span_id": context.current_span_id,
                "work_unit_id": work_unit_id,
                "attempt": attempt,
                "text": presentation["started_text"],
                "payload": presentation["span_payload"],
            },
        ),
    )
    return work_unit, span


def finish_work_observation(
    boundary: ExecutionBoundary,
    records: tuple[WorkUnitRecord, SpanRecord],
    status: WorkUnitStatus,
    presentation: TerminalObservationPresentation,
) -> None:
    """Persist the common work-unit and span terminal sequence."""
    work_unit, span = records
    span_status = SpanStatus(status.value)
    work_event = f"work_unit.{status.value}"
    span_event = f"span.{span_status.value}"
    transition_work_unit(boundary, work_unit, status)
    append_observation_fact(
        boundary,
        _terminal_fact(
            presentation,
            {
                "event_type": work_event,
                "status": status.value,
                "span": span,
                "work_unit": work_unit,
                "payload": presentation["work_payload"],
            },
        ),
    )
    transition_span(boundary, span, span_status)
    append_observation_fact(
        boundary,
        _terminal_fact(
            presentation,
            {
                "event_type": span_event,
                "status": span_status.value,
                "span": span,
                "work_unit": work_unit,
                "payload": presentation["span_payload"],
            },
        ),
    )


def _start_fact(
    presentation: StartObservationPresentation,
    values: _StartFactValues,
) -> ObservationFact:
    event_type = values["event_type"]
    work_unit_id = values["work_unit_id"]
    span_id = values["span_id"]
    suffix = _fact_suffix(presentation["style"], event_type)
    fact: ObservationFact = {
        "event_type": event_type,
        "status": values["status"],
        "source": presentation["source"],
        "span_id": span_id,
        "parent_span_id": values["parent_span_id"],
        "work_unit_id": work_unit_id,
        "attempt": values["attempt"],
        "summary_key": f"{presentation['summary_prefix']}.{suffix}",
        "text": values["text"],
        "public_payload": values["payload"],
    }
    fact["idempotency_key"] = _fact_idempotency_key(
        presentation["style"], fact
    )
    return fact


def _terminal_fact(
    presentation: TerminalObservationPresentation,
    values: _TerminalFactValues,
) -> ObservationFact:
    event_type = values["event_type"]
    status = values["status"]
    span = values["span"]
    work_unit = values["work_unit"]
    suffix = _fact_suffix(presentation["style"], event_type)
    fact: ObservationFact = {
        "event_type": event_type,
        "status": status,
        "source": presentation["source"],
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "work_unit_id": work_unit.work_unit_id,
        "attempt": presentation["attempt"],
        "summary_key": f"{presentation['summary_prefix']}.{suffix}",
        "text": presentation["text"],
        "public_payload": values["payload"],
    }
    fact["idempotency_key"] = _fact_idempotency_key(
        presentation["style"], fact
    )
    return fact


def _fact_suffix(style: FactStyle, event_type: str) -> str:
    if style == "tool":
        return event_type.rsplit(".", 1)[-1]
    return {
        "work_unit.registered": "registered",
        "span.created": "span:created",
        "work_unit.attempt_started": "attempt:1:started",
        "span.started": "span:started",
    }.get(
        event_type,
        (
            f"work:{event_type.rsplit('.', 1)[-1]}"
            if event_type.startswith("work_unit.")
            else f"span:{event_type.rsplit('.', 1)[-1]}"
        ),
    )


def _fact_idempotency_key(
    style: FactStyle,
    fact: ObservationFact,
) -> str:
    event_type = fact["event_type"]
    span_id = fact["span_id"]
    work_unit_id = fact.get("work_unit_id")
    if work_unit_id is None:
        raise ValueError("work observation fact requires work_unit_id")
    attempt = fact["attempt"]
    status = fact["status"]
    if style == "runtime":
        return f"work:{work_unit_id}:{_fact_suffix(style, event_type)}"
    if event_type == "work_unit.registered":
        return f"work:{work_unit_id}:registered"
    if event_type == "work_unit.attempt_started":
        return f"work:{work_unit_id}:attempt:{attempt}:started"
    if event_type.startswith("work_unit."):
        return f"work:{work_unit_id}:attempt:{attempt}:{status}"
    return f"span:{span_id}:{event_type.rsplit('.', 1)[-1]}"
