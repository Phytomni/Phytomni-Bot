# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Provider boundaries publish durable, idempotent V2 facts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from tests.support.execution_runtime_v2 import (
    ExecutionStartRequest,
    build_local_graph_runtime,
    run_execution_start,
)

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import ExecutionStatus
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
    instrument_provider_cancellation,
    instrument_provider_observation,
    instrument_provider_submission,
    record_provider_retry,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_provider_submit_poll_retry_cancel_and_terminal_are_observed(
    tmp_path: Path,
) -> None:
    """Verify provider submit poll retry cancel and terminal are observed."""

    db_path = str(tmp_path / "provider.db")
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    provider_idempotency_keys: list[str] = []

    async def start_handler(context, command, services):
        del context, command, services

        async def submit_primary():
            record_provider_retry(delay_ms=25, code="provider_busy")
            return {
                "task_id": "private-provider-task-primary",
                "private": "submission-secret",
            }

        async def submit_primary_idempotently(key: str):
            provider_idempotency_keys.append(key)
            assert work.list_due_work_units(
                now=datetime.now(UTC),
                limit=10,
            )
            return await submit_primary()

        primary = await instrument_provider_submission(
            provider_kind="analysis_task_platform",
            operation_key="provider.analysis.submit",
            call=submit_primary,
            call_with_idempotency=submit_primary_idempotently,
            identity_from_result=lambda result: result["task_id"],
            max_attempts=3,
        )
        await instrument_provider_observation(
            provider_kind="analysis_task_platform",
            provider_task_id=primary["task_id"],
            source_revision=1,
            observed_status="running",
        )
        # More than one thousand unchanged polls refresh liveness without
        # extending the durable public ledger.
        for _ in range(1_001):
            await instrument_provider_observation(
                provider_kind="analysis_task_platform",
                provider_task_id=primary["task_id"],
                source_revision=1,
                observed_status="running",
            )
        await instrument_provider_observation(
            provider_kind="analysis_task_platform",
            provider_task_id=primary["task_id"],
            source_revision=2,
            observed_status="succeeded",
        )

        async def submit_cancelled():
            return {"task_id": "private-provider-task-cancelled"}

        cancelled = await instrument_provider_submission(
            provider_kind="analysis_task_platform",
            operation_key="provider.analysis.submit",
            call=submit_cancelled,
            identity_from_result=lambda result: result["task_id"],
        )
        await instrument_provider_cancellation(
            provider_kind="analysis_task_platform",
            provider_task_id=cancelled["task_id"],
            call=lambda: asyncio.sleep(0),
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal, work)
    assert (
        run_execution_start(
            runtime, ExecutionStartRequest("turn-provider", "c" * 64)
        ).status
        is ExecutionStatus.SUCCEEDED
    )
    assert len(provider_idempotency_keys) == 1
    assert provider_idempotency_keys[0].startswith("phyto:turn-provider:")
    assert provider_idempotency_keys[0].endswith(":1")

    page = journal.list_events("turn-provider", owner="alice", limit=100)
    assert page is not None
    provider_events = [
        event for event in page.items if event.source.value == "provider"
    ]
    types = [event.type.value for event in provider_events]
    assert types.count("work_unit.registered") == 4
    assert types.count("work_unit.submitted") == 4
    assert types.count("work_unit.acknowledged") == 5
    assert types.count("work_unit.retry_scheduled") == 1
    assert types.count("work_unit.succeeded") == 3
    assert types.count("work_unit.cancellation_requested") == 1
    assert types.count("work_unit.cancellation_confirmed") == 1
    provider_contacts = [
        event
        for event in provider_events
        if event.type.value == "work_unit.progress"
        and getattr(event.public_payload, "observation", None)
        == "provider_contact"
    ]
    assert provider_contacts == []
    primary_work = work.find_work_unit_by_provider_task_id(
        "turn-provider",
        "private-provider-task-primary",
        owner="alice",
        provider_kind="analysis_task_platform",
    )
    assert primary_work is not None
    assert primary_work.provider_trace_contact_at is not None
    observations = {
        event.status.value: event.summary.text
        for event in provider_events
        if event.summary.key.startswith("remote.reconcile.")
    }
    assert observations["running"] == "External analysis is running"
    assert observations["succeeded"] == "External analysis completed"
    running_revisions = [
        event
        for event in provider_events
        if event.idempotency_key and ":revision:1:" in event.idempotency_key
    ]
    assert len(running_revisions) == 1
    public_text = str([event.to_public_dict() for event in provider_events])
    assert "private-provider-task" not in public_text
    assert "submission-secret" not in public_text


def test_analysis_submission_ack_does_not_complete_remote_analysis(
    tmp_path: Path,
) -> None:
    """Verify analysis submission ack does not complete remote analysis."""

    db_path = str(tmp_path / "analysis-submission-boundary.db")
    journal = SQLiteExecutionJournal(db_path)

    async def start_handler(context, command, services):
        del context, command, services
        await instrument_provider_submission(
            provider_kind="analysis_task_platform",
            operation_key="remote.analysis",
            call=lambda: asyncio.sleep(
                0, result={"task_id": "still-running-provider-task"}
            ),
            identity_from_result=lambda result: result["task_id"],
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="queued"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal)
    assert (
        run_execution_start(
            runtime,
            ExecutionStartRequest(
                "turn-analysis-submission-boundary", "a" * 64
            ),
        ).status.value
        == "succeeded"
    )

    with sqlite_transaction(db_path) as connection:
        rows = connection.execute(
            "SELECT work_unit_id, operation_key, status, provider_task_id "
            "FROM execution_work_units ORDER BY operation_key"
        ).fetchall()
        span_rows = connection.execute(
            "SELECT w.operation_key, s.status FROM execution_spans s "
            "JOIN execution_work_units w "
            "ON w.owner_ref = s.owner_ref "
            "AND w.execution_id = s.execution_id "
            "AND w.work_unit_id = s.work_unit_id"
        ).fetchall()

    by_operation = {
        operation_key: {
            "work_unit_id": work_unit_id,
            "status": status,
            "provider_task_id": provider_task_id,
        }
        for work_unit_id, operation_key, status, provider_task_id in rows
    }
    assert set(by_operation) == {"remote.analysis", "remote.submit"}
    analysis = by_operation["remote.analysis"]
    submission = by_operation["remote.submit"]
    assert analysis["status"] == "acknowledged"
    assert analysis["provider_task_id"] == "still-running-provider-task"
    assert submission["status"] == "succeeded"
    assert submission["provider_task_id"] is None
    assert dict(span_rows) == {
        "remote.analysis": "running",
        "remote.submit": "succeeded",
    }

    page = journal.list_events(
        "turn-analysis-submission-boundary", owner="alice", limit=100
    )
    assert page is not None
    submission_success = [
        event
        for event in page.items
        if event.type.value == "span.succeeded"
        and event.summary.text == "Provider submission acknowledged"
    ]
    assert len(submission_success) == 1
    assert submission_success[0].work_unit_id == submission["work_unit_id"]
    assert all(
        event.work_unit_id != analysis["work_unit_id"]
        for event in page.items
        if event.type.value in {"span.succeeded", "work_unit.succeeded"}
    )


def test_provider_is_not_called_when_submission_intent_cannot_persist(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify provider is not called when submission intent cannot persist."""

    db_path = str(tmp_path / "provider-fail-closed.db")
    work = SQLiteExecutionWorkRepository(db_path)
    called = False

    async def provider_call():
        nonlocal called
        called = True
        return {"task_id": "must-not-exist"}

    async def start_handler(context, command, services):
        del context, command, services
        await instrument_provider_submission(
            provider_kind="provider",
            operation_key="provider.submit",
            call=provider_call,
            identity_from_result=lambda result: result["task_id"],
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="bad"))

    runtime = build_local_graph_runtime(db_path, start_handler, work=work)

    def fail_create_work_unit(spec):
        del spec
        raise OSError("disk unavailable")

    monkeypatch.setattr(work, "create_work_unit", fail_create_work_unit)
    outcome = run_execution_start(
        runtime,
        ExecutionStartRequest("turn-provider-fail-closed", "d" * 64),
    )

    assert called is False
    assert outcome.status.value == "failed"


def test_analysis_submission_has_one_canonical_provider_boundary() -> None:
    """Verify analysis submission has one canonical provider boundary."""
    root = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"
    analyst_graph = (root / "agents" / "analyst" / "graph.py").read_text(
        encoding="utf-8"
    )
    remote_analysis = (
        root / "agents" / "shared" / "remote_analysis.py"
    ).read_text(encoding="utf-8")
    deep_remote = (root / "agents" / "deep_genome" / "remote_io.py").read_text(
        encoding="utf-8"
    )

    assert analyst_graph.count("instrument_provider_submission(") == 1
    assert "instrument_provider_submission" not in remote_analysis
    assert "instrument_provider_submission" not in deep_remote


def test_provider_cancellation_preserves_best_effort_and_unsupported_work(
    tmp_path: Path,
) -> None:
    """Verify provider cancellation preserves best effort and unsupported
    work."""

    db_path = str(tmp_path / "provider-cancel-outcomes.db")
    work = SQLiteExecutionWorkRepository(db_path)

    async def start_handler(context, command, services):
        del context, command, services

        def cancellation_outcome(
            value: str,
        ) -> Literal["confirmed", "best_effort", "unsupported"]:
            return cast(
                Literal["confirmed", "best_effort", "unsupported"], value
            )

        for index, outcome in enumerate(("best_effort", "unsupported")):
            result = await instrument_provider_submission(
                provider_kind="provider",
                operation_key=f"provider.submit.{index}",
                call=lambda index=index: asyncio.sleep(
                    0, result={"task_id": f"task-{index}"}
                ),
                identity_from_result=lambda value: value["task_id"],
            )
            await instrument_provider_cancellation(
                provider_kind="provider",
                provider_task_id=result["task_id"],
                call=lambda outcome=outcome: asyncio.sleep(0, result=outcome),
                outcome_from_result=cancellation_outcome,
            )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, work=work)
    outcome = run_execution_start(
        runtime,
        ExecutionStartRequest("turn-provider-cancel-outcomes", "e" * 64),
    )
    assert outcome.status.value == "succeeded"
    for index, expected in enumerate(("best_effort", "unsupported")):
        unit = work.find_work_unit_by_provider_task_id(
            "turn-provider-cancel-outcomes",
            f"task-{index}",
            owner="alice",
            provider_kind="provider",
        )
        assert unit is not None
        assert unit.cancellation_state == expected
        assert unit.status.value == "acknowledged"


def test_lost_provider_ack_binding_keeps_durable_submitted_intent(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify lost provider ack binding keeps durable submitted intent."""

    db_path = str(tmp_path / "lost-ack.db")
    work = SQLiteExecutionWorkRepository(db_path)
    calls: list[str] = []

    async def start_handler(context, command, services):
        del context, command, services

        async def submit(key: str):
            calls.append(key)
            return {"task_id": "provider-created-once"}

        await instrument_provider_submission(
            provider_kind="provider",
            operation_key="provider.submit",
            call=lambda: asyncio.sleep(0, result={"task_id": "unused"}),
            call_with_idempotency=submit,
            identity_from_result=lambda result: result["task_id"],
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="bad"))

    runtime = build_local_graph_runtime(db_path, start_handler, work=work)

    def lose_ack(*args, **kwargs):
        del args, kwargs
        raise OSError("database acknowledgement unavailable")

    monkeypatch.setattr(work, "bind_provider", lose_ack)
    outcome = run_execution_start(
        runtime,
        ExecutionStartRequest("turn-lost-ack", "f" * 64),
    )
    assert outcome.status.value == "failed"
    assert len(calls) == 1
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT status, provider_task_id FROM execution_work_units"
        ).fetchone()
    assert row == ("submitted", None)
