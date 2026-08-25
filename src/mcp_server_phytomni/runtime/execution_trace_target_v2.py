# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable owner-authorized handles for grouped public work traces."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ..public_agent_catalog import public_agent_spec
from .execution_journal_store_v2 import ExecutionJournal
from .execution_journal_v2 import (
    ExecutionEventV2,
    ProgressPublicPayload,
    PublicOperationAttemptV2,
    PublicOperationProgressV2,
    PublicOperationRecordV2,
    PublicOperationSummaryV2,
    PublicTarget,
    PublicTextPayload,
)
from .execution_trace_detail import OPERATION_PRESENTER_REGISTRY


class _PublicTraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicTraceOperationV1(_PublicTraceModel):
    """Target header without the journal's private grouping identity."""

    operation_id: str = Field(min_length=1, max_length=128)
    operation_key: str = Field(min_length=1, max_length=128)
    label_key: str = Field(min_length=1, max_length=128)
    fallback_label: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=32)
    started_at: str
    last_observation_at: str
    completed_at: str | None = None
    duration_ms: int = Field(ge=0)
    current_attempt: int = Field(ge=1)
    attempts: tuple[PublicOperationAttemptV2, ...] = Field(max_length=8)
    progress: PublicOperationProgressV2 | None = None
    detail: dict[str, int | bool | str] = Field(max_length=16)
    summary: PublicOperationSummaryV2 | None = None
    target: PublicTarget


class PublicTraceFeedItemV1(_PublicTraceModel):
    """One chronological, reducer-friendly semantic public fact."""

    schema_version: Literal[1] = 1
    item_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=1)
    kind: Literal["phase", "tool", "reasoning_summary", "decision"]
    operation_key: str = Field(min_length=1, max_length=128)
    label_key: str = Field(min_length=1, max_length=128)
    fallback_label: str = Field(min_length=1, max_length=128)
    status: Literal[
        "pending", "running", "succeeded", "failed", "cancelled", "timed_out"
    ]
    attempt: int = Field(ge=1, le=20)
    occurred_at: str
    duration_ms: int | None = Field(default=None, ge=0)
    progress: PublicOperationProgressV2 | None = None
    attempts: tuple[PublicOperationAttemptV2, ...] = Field(
        default=(), max_length=8
    )
    detail: dict[str, int | bool | str] = Field(
        default_factory=dict, max_length=16
    )
    summary: str | None = Field(default=None, min_length=1, max_length=512)
    target: PublicTarget | None = None


class PublicTraceResolutionV1(_PublicTraceModel):
    """Bounded owner-authorized resolution of one grouped operation."""

    schema_version: Literal[1] = 1
    target: PublicTarget
    health: Literal["healthy", "degraded", "unavailable"]
    operation: PublicTraceOperationV1
    last_semantic_activity_at: str | None = None
    last_provider_contact_at: str | None = None
    items: tuple[PublicTraceFeedItemV1, ...] = Field(max_length=100)
    next_after_seq: int = Field(ge=0)
    has_more: bool


TraceHealthV1 = Literal["healthy", "degraded", "unavailable"]
TraceItemStatusV1 = Literal[
    "pending", "running", "succeeded", "failed", "cancelled", "timed_out"
]


def trace_target_for_operation(
    *,
    agent_slug: str,
    operation_key: str,
    execution_id: str,
    work_unit_id: str,
) -> dict[str, str] | None:
    """Return a non-reversible handle for catalog-declared trace operations."""
    agent = public_agent_spec(agent_slug)
    if agent is None or operation_key not in agent.trace_target_operations:
        return None
    digest = hashlib.sha256(
        (
            "trace-target-v1\x1f"
            f"{agent_slug}\x1f{operation_key}\x1f"
            f"{execution_id}\x1f{work_unit_id}"
        ).encode()
    ).digest()
    opaque_id = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return {"kind": "trace", "id": f"trc_{opaque_id}"}


def resolve_trace_target(
    journal: ExecutionJournal,
    *,
    owner: str,
    execution_id: str,
    target_id: str,
    after_seq: int = 0,
    limit: int = 50,
) -> PublicTraceResolutionV1 | None:
    """Rebuild one safe trace page exclusively from canonical journal facts."""
    if after_seq < 0:
        raise ValueError("after_seq must be non-negative")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    projection = journal.get_projection(execution_id, owner=owner)
    operation = next(
        (
            candidate
            for candidate in projection.operations
            if candidate.target is not None
            and candidate.target.kind.value == "trace"
            and candidate.target.id == target_id
        ),
        None,
    )
    if operation is None or operation.target is None:
        return None
    events = _all_events(journal, owner=owner, execution_id=execution_id)
    analysis_span_id = next(
        (
            event.span_id
            for event in events
            if event.work_unit_id == operation.work_unit_id
            and event.type.value.startswith("span.")
            and getattr(event.public_payload, "phase", None)
            == "remote.analysis"
        ),
        None,
    )
    descendant_spans = {analysis_span_id} if analysis_span_id else set()
    for event in events:
        if event.parent_span_id in descendant_spans:
            descendant_spans.add(event.span_id)

    operations_by_work = {
        candidate.work_unit_id: candidate
        for candidate in projection.operations
    }
    items: list[PublicTraceFeedItemV1] = []
    for event in events:
        item = _trace_feed_item(
            event,
            operations_by_work=operations_by_work,
            descendant_spans=descendant_spans,
        )
        if item is not None:
            items.append(item)
    last_activity = items[-1].occurred_at if items else None
    remaining = [item for item in items if item.seq > after_seq]
    page_items = tuple(remaining[:limit])
    next_after_seq = page_items[-1].seq if page_items else after_seq
    health: TraceHealthV1 = (
        "healthy"
        if projection.tracking_health.value == "healthy"
        else "degraded"
    )
    return PublicTraceResolutionV1(
        target=operation.target,
        health=health,
        operation=PublicTraceOperationV1.model_validate(
            operation.model_dump(exclude={"schema_version", "work_unit_id"})
        ),
        last_semantic_activity_at=last_activity,
        last_provider_contact_at=(
            projection.execution_stage.clocks.last_provider_contact_at
        ),
        items=page_items,
        next_after_seq=next_after_seq,
        has_more=len(remaining) > len(page_items),
    )


def _all_events(
    journal: ExecutionJournal,
    *,
    owner: str,
    execution_id: str,
) -> tuple[ExecutionEventV2, ...]:
    events: list[ExecutionEventV2] = []
    cursor = 0
    while True:
        page = journal.list_events(
            execution_id,
            owner=owner,
            after_seq=cursor,
            limit=200,
        )
        if page is None:
            return ()
        events.extend(page.items)
        if not page.has_more:
            return tuple(events)
        if page.next_after_seq <= cursor:
            raise RuntimeError("non_advancing_trace_cursor")
        cursor = page.next_after_seq


def _trace_feed_item(
    event: ExecutionEventV2,
    *,
    operations_by_work: Mapping[str, PublicOperationRecordV2],
    descendant_spans: set[str],
) -> PublicTraceFeedItemV1 | None:
    event_type = event.type.value
    if event_type in {"reasoning.summary", "decision.note"}:
        if (
            event.span_id not in descendant_spans
            and not event.summary.key.startswith("gene_network.")
        ):
            return None
        summary_kind: Literal["reasoning_summary", "decision"] = (
            "reasoning_summary"
            if event_type == "reasoning.summary"
            else "decision"
        )
        if not isinstance(event.public_payload, PublicTextPayload):
            return None
        return PublicTraceFeedItemV1(
            item_id=event.event_id,
            seq=event.seq,
            kind=summary_kind,
            operation_key=event.summary.key,
            label_key=f"execution.trace.{summary_kind}",
            fallback_label=(
                "Reasoning summary"
                if summary_kind == "reasoning_summary"
                else "Decision"
            ),
            status="running",
            attempt=event.attempt,
            occurred_at=event.occurred_at,
            summary=event.public_payload.text,
        )
    if not event_type.startswith("work_unit."):
        return None
    if event.work_unit_id is None:
        return None
    operation = operations_by_work.get(event.work_unit_id)
    if operation is None:
        return None
    presenter = OPERATION_PRESENTER_REGISTRY.resolve(operation.operation_key)
    if presenter.semantic_kind not in {"phase", "tool"}:
        return None
    if (
        event.span_id not in descendant_spans
        and event.parent_span_id not in descendant_spans
    ):
        return None
    progress = None
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
    status = _trace_status(event.status.value)
    operation_kind: Literal["phase", "tool"] = (
        "phase" if presenter.semantic_kind == "phase" else "tool"
    )
    return PublicTraceFeedItemV1(
        item_id=operation.operation_id,
        seq=event.seq,
        kind=operation_kind,
        operation_key=operation.operation_key,
        label_key=operation.label_key,
        fallback_label=operation.fallback_label,
        status=status,
        attempt=event.attempt,
        occurred_at=event.occurred_at,
        duration_ms=getattr(event.public_payload, "duration_ms", None),
        progress=progress,
        attempts=operation.attempts,
        detail=operation.detail,
        summary=(
            event.summary.text
            if status in {"succeeded", "failed", "cancelled", "timed_out"}
            else None
        ),
        target=event.target,
    )


def _trace_status(value: str) -> TraceItemStatusV1:
    if value in {"queued", "pending", "submitted"}:
        return "pending"
    if value in {"dispatching", "acknowledged", "running"}:
        return "running"
    if value in {"succeeded", "failed", "cancelled", "timed_out"}:
        return cast(TraceItemStatusV1, value)
    return "running"


__all__ = [
    "PublicTraceFeedItemV1",
    "PublicTraceOperationV1",
    "PublicTraceResolutionV1",
    "resolve_trace_target",
    "trace_target_for_operation",
]
