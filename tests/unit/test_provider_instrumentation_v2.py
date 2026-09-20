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


def test_provider_submit_poll_retry_cancel_and_terminal_are_observed(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.public_agent_catalog import public_agent_spec
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
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

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    spec = public_agent_spec("knowledge")
    assert spec is not None
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-provider",
            fingerprint_version=1,
            fingerprint="c" * 64,
            command=ExecutionCommand(
                agent_slug=spec.slug,
                arguments={"query": "rice"},
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "succeeded"
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
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
        instrument_provider_submission,
    )
    from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

    db_path = str(tmp_path / "analysis-submission-boundary.db")
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)

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

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-analysis-submission-boundary",
            fingerprint_version=1,
            fingerprint="a" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "succeeded"

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
        operation_key: (work_unit_id, status, provider_task_id)
        for work_unit_id, operation_key, status, provider_task_id in rows
    }
    assert set(by_operation) == {"remote.analysis", "remote.submit"}
    analysis_id, analysis_status, analysis_provider_id = by_operation[
        "remote.analysis"
    ]
    submit_id, submit_status, submit_provider_id = by_operation[
        "remote.submit"
    ]
    assert analysis_status == "acknowledged"
    assert analysis_provider_id == "still-running-provider-task"
    assert submit_status == "succeeded"
    assert submit_provider_id is None
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
    assert submission_success[0].work_unit_id == submit_id
    assert all(
        event.work_unit_id != analysis_id
        for event in page.items
        if event.type.value in {"span.succeeded", "work_unit.succeeded"}
    )


def test_provider_is_not_called_when_submission_intent_cannot_persist(
    tmp_path: Path, monkeypatch
) -> None:
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
        instrument_provider_submission,
    )

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

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=SQLiteExecutionJournal(db_path),
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )

    def fail_create_work_unit(spec):
        del spec
        raise OSError("disk unavailable")

    monkeypatch.setattr(work, "create_work_unit", fail_create_work_unit)
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-provider-fail-closed",
            fingerprint_version=1,
            fingerprint="d" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )

    assert called is False
    assert outcome.status.value == "failed"


def test_analysis_submission_has_one_canonical_provider_boundary() -> None:
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
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
        instrument_provider_cancellation,
        instrument_provider_submission,
    )

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
                call=lambda: asyncio.sleep(0, result=outcome),
                outcome_from_result=cancellation_outcome,
            )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=SQLiteExecutionJournal(db_path),
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-provider-cancel-outcomes",
            fingerprint_version=1,
            fingerprint="e" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
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
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
        instrument_provider_submission,
    )
    from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

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

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=SQLiteExecutionJournal(db_path),
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )

    def lose_ack(*args, **kwargs):
        del args, kwargs
        raise OSError("database acknowledgement unavailable")

    monkeypatch.setattr(work, "bind_provider", lose_ack)
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-lost-ack",
            fingerprint_version=1,
            fingerprint="f" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "failed"
    assert len(calls) == 1
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT status, provider_task_id FROM execution_work_units"
        ).fetchone()
    assert row == ("submitted", None)
