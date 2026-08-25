# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic projection folding for ordered public execution events."""

from __future__ import annotations

from collections.abc import Iterable

from .execution_events import (
    ExecutionEventStatus,
    ExecutionEventV1,
    PublicArtifactPayload,
    PublicInputRequiredPayload,
    PublicPhaseFailurePayload,
    PublicPhasePayload,
    PublicPhaseProgressPayload,
    PublicResultItem,
    PublicTerminalState,
    PublicTodoSnapshotPayload,
    RunEventProjectionV1,
    TerminalExecutionStatus,
)


def empty_run_event_projection(run_id: str) -> RunEventProjectionV1:
    """Return the stable initial projection for an event-less run."""
    return RunEventProjectionV1(
        schema_version=1,
        run_id=run_id,
        latest_seq=0,
        status="queued",
    )


def apply_execution_event(
    projection: RunEventProjectionV1,
    event: ExecutionEventV1,
) -> RunEventProjectionV1:
    """Apply one later event without consulting mutable runtime state."""
    if event.run_id != projection.run_id:
        raise ValueError("projection_run_mismatch")
    if event.seq <= projection.latest_seq:
        raise ValueError("projection_sequence_mismatch")

    status = projection.status
    terminal = projection.terminal
    input_required = projection.input_required
    phase = projection.phase
    todos = projection.todos
    results = projection.results

    lifecycle_status_by_kind: dict[str, ExecutionEventStatus] = {
        "run.accepted": "queued",
        "run.started": "running",
        "run.waiting_input": "waiting",
        "run.resumed": "running",
        "run.succeeded": "succeeded",
        "run.failed": "failed",
        "run.cancelled": "cancelled",
    }
    lifecycle_status = lifecycle_status_by_kind.get(event.kind)
    if lifecycle_status is not None:
        status = lifecycle_status

    if event.kind in {"run.succeeded", "run.failed", "run.cancelled"}:
        terminal_status_by_kind: dict[str, TerminalExecutionStatus] = {
            "run.succeeded": "succeeded",
            "run.failed": "failed",
            "run.cancelled": "cancelled",
        }
        terminal_status = terminal_status_by_kind[event.kind]
        status = terminal_status
        terminal = PublicTerminalState(
            status=terminal_status, event_id=event.event_id
        )

    if isinstance(
        event.payload,
        (
            PublicPhasePayload,
            PublicPhaseProgressPayload,
            PublicPhaseFailurePayload,
        ),
    ):
        phase = event.payload.phase

    if isinstance(event.payload, PublicTodoSnapshotPayload):
        todos = event.payload.items

    if event.kind == "input.required":
        assert isinstance(event.payload, PublicInputRequiredPayload)
        input_required = event.payload
        status = "waiting"
    elif event.kind == "input.resolved":
        input_required = None

    if (
        event.kind == "artifact.published"
        and isinstance(event.payload, PublicArtifactPayload)
        and event.target is not None
        and all(item.event_id != event.event_id for item in results)
    ):
        results = (
            *results,
            PublicResultItem(
                event_id=event.event_id,
                name=event.payload.name,
                media_type=event.payload.media_type,
                size_bytes=event.payload.size_bytes,
                target=event.target,
            ),
        )

    return RunEventProjectionV1(
        schema_version=1,
        run_id=projection.run_id,
        latest_seq=event.seq,
        status=status,
        phase=phase,
        todos=todos,
        results=results,
        input_required=input_required,
        terminal=terminal,
    )


def fold_execution_events(
    run_id: str,
    events: Iterable[ExecutionEventV1],
) -> RunEventProjectionV1:
    """Rebuild a projection from the complete ordered event ledger."""
    projection = empty_run_event_projection(run_id)
    for event in events:
        projection = apply_execution_event(projection, event)
    return projection
