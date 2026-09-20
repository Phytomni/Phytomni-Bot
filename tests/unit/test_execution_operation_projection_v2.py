# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Operation lifecycle facts fold into one bounded authoritative record."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, NotRequired, TypedDict, Unpack

from tests.support.execution_projection_v2 import execution_message_payload

from mcp_server_phytomni.runtime.execution_event_limits import (
    DEFAULT_EXECUTION_EVENT_LIMITS,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    parse_execution_event_intent_v2,
    parse_execution_event_v2,
)
from mcp_server_phytomni.runtime.execution_projection_v2 import (
    apply_execution_event_v2,
    empty_execution_projection_v2,
    fold_execution_events_v2,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_trace_detail import (
    OPERATION_PRESENTER_REGISTRY,
)


class _EventOptions(TypedDict):
    """Optional fields accepted by the operation event factory."""

    attempt: NotRequired[int]
    operation_key: NotRequired[str]


def _event(
    seq: int,
    event_type: str,
    status: str,
    payload: dict[str, object],
    **options: Unpack[_EventOptions],
):
    attempt = options.get("attempt", 1)
    operation_key = options.get("operation_key", "review.retrieve_dimension")

    return parse_execution_event_v2(
        {
            "schema_version": 2,
            "event_id": f"evt-operation-{seq}",
            "execution_id": "turn-operation-projection",
            "seq": seq,
            "type": event_type,
            "status": status,
            "occurred_at": f"2026-08-22T05:00:{seq:02d}Z",
            "source": "runtime",
            "span_id": f"span-attempt-{attempt}",
            "parent_span_id": "span-root",
            "work_unit_id": "work-review-2",
            "attempt": attempt,
            "summary": {
                "key": f"{operation_key}.{event_type}",
                "text": event_type.replace("_", " ").replace(".", " "),
            },
            "public_payload": payload,
            "target": None,
            "idempotency_key": f"operation-fact:{seq}",
        }
    )


def _lifecycle_events() -> list[Any]:
    return [
        _event(
            1,
            "work_unit.registered",
            "queued",
            {
                "operation_key": "review.retrieve_dimension",
                "detail": {"ordinal": 2, "total": 4},
            },
        ),
        _event(
            2,
            "work_unit.attempt_started",
            "running",
            {"operation_key": "review.retrieve_dimension"},
        ),
        _event(
            3,
            "work_unit.failed",
            "failed",
            {
                "code": "provider_retryable",
                "retryable": True,
                "duration_ms": 2000,
            },
        ),
        _event(
            4,
            "work_unit.retry_scheduled",
            "retry_scheduled",
            {
                "delay_ms": 1000,
                "next_attempt": 2,
                "code": "provider_retryable",
            },
        ),
        _event(
            5,
            "work_unit.attempt_started",
            "running",
            {"operation_key": "review.retrieve_dimension"},
            attempt=2,
        ),
        _event(
            6,
            "work_unit.progress",
            "running",
            {
                "phase": "review.retrieve_dimension",
                "completed": 2,
                "total": 4,
                "unit": "dimensions",
            },
            attempt=2,
        ),
        _event(
            7,
            "work_unit.succeeded",
            "succeeded",
            {
                "operation_key": "review.retrieve_dimension",
                "duration_ms": 2000,
                "detail": {"ordinal": 2, "total": 4},
            },
            attempt=2,
        ),
    ]


def test_projection_groups_attempt_retry_progress_and_terminal_facts() -> None:
    """Verify projection groups attempt retry progress and terminal facts."""

    projection = fold_execution_events_v2(
        "turn-operation-projection", _lifecycle_events()
    )

    assert len(projection.operations) == 1
    operation = projection.operations[0]
    assert operation.work_unit_id == "work-review-2"
    assert operation.operation_key == "review.retrieve_dimension"
    assert operation.label_key == (
        "execution.operation.review.retrieveDimension"
    )
    assert operation.status == "succeeded"
    assert operation.current_attempt == 2
    assert operation.detail == {"ordinal": 2, "total": 4}
    assert operation.progress is not None
    assert operation.progress.model_dump() == {
        "completed": 2,
        "total": 4,
        "unit": "dimensions",
    }
    assert [attempt.status for attempt in operation.attempts] == [
        "retry_scheduled",
        "succeeded",
    ]
    assert operation.attempts[0].failure is not None
    assert operation.attempts[0].failure.code == "provider_retryable"
    assert operation.attempts[0].retry is not None
    assert operation.attempts[0].retry.delay_ms == 1000
    assert operation.completed_at == "2026-08-22T05:00:07Z"


def test_incremental_and_history_operation_projection_are_identical() -> None:
    """Verify incremental and history operation projection are identical."""

    events = _lifecycle_events()
    incremental = empty_execution_projection_v2("turn-operation-projection")
    for event in events:
        incremental = apply_execution_event_v2(incremental, event)

    assert incremental == fold_execution_events_v2(
        "turn-operation-projection", events
    )
    assert incremental.execution_stage.stage == "scientific_execution"
    assert incremental.execution_stage.pending_status_key == (
        "execution.pending.running"
    )
    assert incremental.execution_stage.clocks.last_execution_fact_at == (
        "2026-08-22T05:00:07Z"
    )


def test_gene_network_presenters_are_semantic_and_fail_closed() -> None:
    """Verify gene network presenters are semantic and fail closed."""

    presenter = OPERATION_PRESENTER_REGISTRY.resolve(
        "gene_network.infer_network"
    )
    assert presenter.semantic_kind == "tool"
    assert presenter.label_key == ("execution.trace.geneNetwork.inferNetwork")

    presented = OPERATION_PRESENTER_REGISTRY.present(
        "gene_network.infer_network",
        detail={
            "gene_count": 28,
            "interaction_count": 91,
            "query": "private provider query",
            "result": "private provider result",
        },
        progress={"completed": 12, "total": 28, "unit": "genes"},
    )
    assert presented.detail == {"gene_count": 28, "interaction_count": 91}
    assert presented.progress == {
        "completed": 12,
        "total": 28,
        "unit": "genes",
    }
    assert "private" not in str(presented)

    oversized = OPERATION_PRESENTER_REGISTRY.present(
        "gene_network.infer_network",
        detail={"gene_count": 1_000_001},
    )
    assert not oversized.detail


def test_stage_todos_pending_and_liveness_clocks_share_projection_rules() -> (
    None
):
    """Verify stage todos pending and liveness clocks share projection
    rules."""

    def fact(
        seq: int,
        event_type: str,
        status: str,
        payload: dict[str, object],
        *,
        work_unit_id: str | None = "work-remote",
    ):
        if event_type.startswith("message."):
            text = "done"
            payload = execution_message_payload(
                text,
                output_revision=1,
                message_id="message-stage",
            )
        return parse_execution_event_v2(
            {
                "schema_version": 2,
                "event_id": f"evt-stage-{seq}",
                "execution_id": "turn-stage-projection",
                "seq": seq,
                "type": event_type,
                "status": status,
                "occurred_at": f"2026-08-22T06:00:{seq:02d}Z",
                "source": "runtime",
                "span_id": "span-remote",
                "parent_span_id": "span-root",
                "work_unit_id": work_unit_id,
                "attempt": 1,
                "summary": {"key": f"stage.{seq}", "text": f"Fact {seq}"},
                "public_payload": payload,
                "target": None,
                "idempotency_key": f"stage:{seq}",
            }
        )

    events = [
        fact(
            1,
            "work_unit.submitted",
            "submitted",
            {
                "operation_key": "remote.analysis",
                "detail": {"provider_state": "submitted"},
            },
        ),
        fact(
            2,
            "work_unit.progress",
            "running",
            {
                "phase": "remote.analysis",
                "observation": "provider_contact",
                "elapsed_ms": 1000,
            },
        ),
        fact(
            3,
            "work_unit.acknowledged",
            "running",
            {
                "operation_key": "remote.analysis",
                "detail": {"provider_state": "running"},
            },
        ),
        fact(
            4,
            "work_unit.registered",
            "queued",
            {"operation_key": "remote.reconcile"},
            work_unit_id="work-reconcile",
        ),
        fact(5, "message.completed", "running", {}, work_unit_id=None),
        fact(6, "execution.succeeded", "succeeded", {}, work_unit_id=None),
    ]
    projection = fold_execution_events_v2("turn-stage-projection", events)
    stage = projection.execution_stage

    assert stage.stage == "response_settlement"
    assert stage.root_status == "succeeded"
    assert stage.pending_status_key is None
    assert [todo.status for todo in stage.todos] == ["completed"] * 4
    assert stage.clocks.last_execution_fact_at == "2026-08-22T06:00:06Z"
    assert stage.clocks.last_provider_contact_at == "2026-08-22T06:00:03Z"
    assert stage.clocks.last_stream_contact_at is None


def test_pruning_restart_and_tracking_degradation_preserve_grouped_state(
    tmp_path: Path,
) -> None:
    """Verify pruning restart and tracking degradation preserve grouped
    state."""

    db_path = str(tmp_path / "operation-restart.db")
    execution_id = "turn-operation-restart"
    SQLiteExecutionReservationRepository(db_path).reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint="7" * 64,
        command=ExecutionCommand(
            agent_slug="review", arguments={"query": "rice"}
        ),
    )
    times = iter(f"2026-08-22T07:00:0{seq}Z" for seq in range(1, 6))
    store = SQLiteExecutionJournal(
        db_path,
        clock=lambda: next(times),
        limits=replace(
            DEFAULT_EXECUTION_EVENT_LIMITS,
            max_events_per_run=4,
            progress_coalesce_ms=0,
        ),
    )

    def append(
        event_type: str,
        status: str,
        payload: dict[str, object],
        key: str,
    ) -> None:
        store.append(
            execution_id,
            owner="alice",
            intent=parse_execution_event_intent_v2(
                {
                    "type": event_type,
                    "status": status,
                    "source": "runtime",
                    "span_id": "span-review-attempt",
                    "parent_span_id": "span-root",
                    "work_unit_id": (
                        None
                        if event_type == "tracking.degraded"
                        else "work-review-restart"
                    ),
                    "attempt": 1,
                    "summary": {"key": key, "text": key.replace(".", " ")},
                    "public_payload": payload,
                    "idempotency_key": key,
                }
            ),
        )

    append(
        "work_unit.registered",
        "queued",
        {"operation_key": "review.retrieve_dimension"},
        "restart.registered",
    )
    append(
        "work_unit.attempt_started",
        "running",
        {"operation_key": "review.retrieve_dimension"},
        "restart.started",
    )
    append(
        "work_unit.progress",
        "running",
        {
            "phase": "review.retrieve_dimension",
            "completed": 1,
            "total": 4,
            "unit": "dimensions",
        },
        "restart.progress.1",
    )
    append(
        "work_unit.progress",
        "running",
        {
            "phase": "review.retrieve_dimension",
            "completed": 2,
            "total": 4,
            "unit": "dimensions",
        },
        "restart.progress.2",
    )
    append(
        "tracking.degraded",
        "degraded",
        {
            "health": "degraded",
            "code": "journal_unavailable",
            "retryable": True,
        },
        "restart.tracking.degraded",
    )

    before_restart = store.get_projection(execution_id, owner="alice")
    page = store.list_events(execution_id, owner="alice", limit=10)
    assert page is not None
    assert [event.seq for event in page.items] == [1, 2, 4, 5]
    assert [
        (gap.first_missing_seq, gap.last_missing_seq) for gap in page.gaps
    ] == [(3, 3)]
    replayed = fold_execution_events_v2(execution_id, page.items)
    restarted = SQLiteExecutionJournal(db_path).get_projection(
        execution_id, owner="alice"
    )

    assert restarted == before_restart == replayed
    assert restarted.tracking_health.value == "degraded"
    assert restarted.operations[0].progress is not None
    assert restarted.operations[0].progress.completed == 2
    assert restarted.execution_stage.stage == "scientific_execution"
    assert restarted.execution_stage.clocks.last_execution_fact_at == (
        "2026-08-22T07:00:04Z"
    )
