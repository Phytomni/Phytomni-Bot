# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic, monotonic projection folding for V2 facts."""

from __future__ import annotations

from typing import NotRequired, TypedDict, Unpack

import pytest
from tests.support.execution_projection_v2 import execution_message_payload

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    parse_execution_event_v2,
)
from mcp_server_phytomni.runtime.execution_projection_v2 import (
    apply_execution_event_v2,
    empty_execution_projection_v2,
    fold_execution_events_v2,
)


class _EventOptions(TypedDict):
    """Optional fields accepted by the projection event factory."""

    span_id: NotRequired[str]
    target: NotRequired[dict[str, str] | None]


def _event(
    seq: int,
    event_type: str,
    status: str,
    payload: dict[str, object],
    **options: Unpack[_EventOptions],
):

    return parse_execution_event_v2(
        {
            "schema_version": 2,
            "event_id": f"evt-{seq}",
            "execution_id": "turn-projection",
            "seq": seq,
            "type": event_type,
            "status": status,
            "occurred_at": f"2026-08-19T00:00:{seq:02d}Z",
            "source": "runtime",
            "span_id": options.get("span_id", "span-root"),
            "parent_span_id": None,
            "work_unit_id": None,
            "attempt": 1,
            "summary": {"key": f"activity.{seq}", "text": f"Fact {seq}"},
            "public_payload": payload,
            "target": options.get("target"),
            "idempotency_key": f"projection:{seq}",
        }
    )


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("execution.succeeded", "succeeded"),
        ("execution.partial", "partial"),
        ("execution.failed", "failed"),
        ("execution.cancelled", "cancelled"),
        ("execution.timed_out", "timed_out"),
    ],
)
def test_terminal_status_is_sticky_against_late_running_fact(
    event_type: str, status: str
) -> None:
    """Verify terminal status is sticky against late running fact."""

    payload: dict[str, object] = {}
    if status in {"partial", "failed", "timed_out"}:
        payload = {"code": f"execution_{status}", "retryable": False}
    elif status == "cancelled":
        payload = {"outcome": "confirmed"}
    projection = apply_execution_event_v2(
        empty_execution_projection_v2("turn-projection"),
        _event(1, event_type, status, payload),
    )
    late = apply_execution_event_v2(
        projection,
        _event(
            2,
            "span.started",
            "running",
            {"phase": "late"},
            span_id="span-late",
        ),
    )

    assert late.status.value == status
    assert late.terminal is not None
    assert late.terminal.status == status
    assert late.terminal.event_id == "evt-1"
    assert late.latest_seq == 2


def test_rebuild_projects_activity_todo_results_output_and_tracking() -> None:
    """Verify rebuild projects activity todo results output and tracking."""

    events = (
        _event(1, "execution.admitted", "admitted", {}),
        _event(
            2,
            "span.started",
            "running",
            {"phase": "retrieval"},
            span_id="span-retrieval",
        ),
        _event(
            3,
            "todo.snapshot",
            "running",
            {
                "items": [
                    {
                        "id": "retrieve",
                        "label_key": "todo.retrieve",
                        "status": "completed",
                    },
                    {
                        "id": "report",
                        "label_key": "todo.report",
                        "status": "in_progress",
                    },
                ]
            },
        ),
        _event(
            4,
            "artifact.published",
            "succeeded",
            {"name": "result.csv", "media_type": "text/csv", "size_bytes": 42},
            target={"kind": "artifact", "id": "artifact-result"},
        ),
        _event(
            5,
            "message.snapshot",
            "running",
            execution_message_payload(
                "Draft response",
                output_revision=2,
                message_id="msg-projection-assistant",
            ),
        ),
        _event(
            6,
            "tracking.degraded",
            "degraded",
            {
                "health": "degraded",
                "code": "provider_unavailable",
                "retryable": True,
            },
        ),
        _event(
            7,
            "span.succeeded",
            "succeeded",
            {"phase": "retrieval"},
            span_id="span-retrieval",
        ),
        _event(8, "execution.succeeded", "succeeded", {}),
    )
    rebuilt = fold_execution_events_v2("turn-projection", events)
    incrementally = empty_execution_projection_v2("turn-projection")
    for event in events:
        incrementally = apply_execution_event_v2(incrementally, event)

    assert rebuilt == incrementally
    assert rebuilt.status.value == "succeeded"
    assert not rebuilt.active_span_ids
    assert rebuilt.todo_declared is True
    assert [item.id for item in rebuilt.todos] == ["retrieve", "report"]
    assert [item.id for item in rebuilt.results] == ["evt-4"]
    assert [(target.kind.value, target.id) for target in rebuilt.targets] == [
        ("artifact", "artifact-result")
    ]
    assert (rebuilt.output_revision, rebuilt.output_offset) == (2, 14)
    assert rebuilt.tracking_health.value == "degraded"
    assert rebuilt.terminal is not None
    assert rebuilt.terminal.result_revision == 2


def test_trace_target_is_discoverable_but_never_projected_as_a_result() -> (
    None
):
    """Verify trace target is discoverable but never projected as a result."""

    projected = apply_execution_event_v2(
        empty_execution_projection_v2("turn-projection"),
        _event(
            1,
            "artifact.published",
            "succeeded",
            {
                "name": "provider-trace.json",
                "media_type": "application/json",
                "size_bytes": 42,
            },
            target={
                "kind": "trace",
                "id": "trc_A1b2C3d4E5f6G7h8",
            },
        ),
    )

    assert not projected.results
    assert [
        (target.kind.value, target.id) for target in projected.targets
    ] == [("trace", "trc_A1b2C3d4E5f6G7h8")]


def test_stale_output_and_non_monotonic_sequence_are_rejected() -> None:
    """Verify stale output and non monotonic sequence are rejected."""

    first = apply_execution_event_v2(
        empty_execution_projection_v2("turn-projection"),
        _event(
            1,
            "message.snapshot",
            "running",
            execution_message_payload(
                "new",
                output_revision=2,
                message_id="msg-projection-assistant",
            ),
        ),
    )
    stale = apply_execution_event_v2(
        first,
        _event(
            2,
            "message.snapshot",
            "running",
            execution_message_payload(
                "old",
                output_revision=1,
                message_id="msg-projection-assistant",
            ),
        ),
    )
    assert (stale.output_revision, stale.output_offset) == (2, 3)
    with pytest.raises(ValueError, match="projection_sequence_mismatch"):
        apply_execution_event_v2(
            stale, _event(2, "execution.started", "running", {})
        )
