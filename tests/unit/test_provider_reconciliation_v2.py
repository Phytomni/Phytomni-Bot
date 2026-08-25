# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Provider recovery is durable, revisioned, and read-path independent."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _seed_provider_work(db_path: Path):
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SpanSpec,
        SQLiteExecutionWorkRepository,
        WorkUnitSpec,
        WorkUnitStatus,
    )

    path = str(db_path)
    reservations = SQLiteExecutionReservationRepository(path)
    journal = SQLiteExecutionJournal(path)
    work = SQLiteExecutionWorkRepository(path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="turn-provider-recovery",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=ExecutionCommand(
            agent_slug="analyst", arguments={"query": "rice"}
        ),
    )
    work.create_span(
        SpanSpec(
            owner=reservation.owner,
            execution_id=reservation.execution_id,
            span_id=reservation.root_span_id,
            kind="agent",
            label_key="agent.analyst",
        )
    )
    unit = work.create_work_unit(
        WorkUnitSpec(
            owner=reservation.owner,
            execution_id=reservation.execution_id,
            work_unit_id="work-provider",
            parent_span_id=reservation.root_span_id,
            operation_key="provider.analysis",
            driver="remote_task",
            max_attempts=3,
        )
    )
    unit = work.update_work_unit_status(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        status=WorkUnitStatus.SUBMITTED,
        expected_revision=unit.revision,
    )
    unit = work.bind_provider(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        provider_kind="analysis",
        provider_task_id="private-task-1",
        provider_revision=0,
        expected_revision=unit.revision,
    )
    unit = work.update_work_unit_status(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        status=WorkUnitStatus.ACKNOWLEDGED,
        expected_revision=unit.revision,
    )
    return reservations, journal, work, unit


def test_callback_accelerates_and_poll_duplicate_is_suppressed(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
        ProviderObservation,
        ProviderReconciler,
    )

    reservations, journal, work, unit = _seed_provider_work(
        tmp_path / "provider-reconcile.db"
    )

    async def poll(current):
        assert current.provider_task_id == "private-task-1"
        return ProviderObservation(status="running", source_revision=1)

    reconciler = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis": poll},
    )
    assert reconciler.observe_callback(
        owner=unit.owner,
        execution_id=unit.execution_id,
        provider_kind="analysis",
        provider_task_id="private-task-1",
        observation=ProviderObservation(status="running", source_revision=1),
    )
    current = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert current.status.value == "running"
    assert asyncio.run(reconciler.reconcile(current)) is False

    page = journal.list_events(unit.execution_id, owner=unit.owner, limit=100)
    assert page is not None
    observations = [
        event
        for event in page.items
        if event.idempotency_key == "provider:work-provider:revision:1:running"
    ]
    assert len(observations) == 1


def test_poll_recovery_advances_from_stored_work_without_read_api(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
        ProviderObservation,
        ProviderReconciler,
    )

    reservations, journal, work, unit = _seed_provider_work(
        tmp_path / "provider-poll.db"
    )
    observed: list[tuple[str, int]] = []

    async def poll(current):
        observed.append(
            (current.provider_task_id or "", current.provider_revision)
        )
        return ProviderObservation(status="succeeded", source_revision=7)

    reconciler = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis": poll},
    )
    assert asyncio.run(reconciler.reconcile(unit)) is True
    current = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert observed == [("private-task-1", 0)]
    assert current.status.value == "succeeded"
    assert current.provider_revision == 7

    source = (
        Path(__file__).parents[2]
        / "src/mcp_server_phytomni/runtime/provider_reconciliation_v2.py"
    ).read_text(encoding="utf-8")
    assert "api." not in source
    assert "lifecycle" not in source


def test_status_and_trace_reconcile_on_independent_bounded_cadence(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_intent_v2,
    )
    from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
        ProviderObservation,
        ProviderReconciler,
    )
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceAdapterResult,
        ProviderTraceObservation,
        ProviderTraceRecord,
    )

    reservations, journal, work, unit = _seed_provider_work(
        tmp_path / "provider-trace-reconcile.db"
    )
    now = datetime(2026, 8, 23, tzinfo=UTC)
    status_polls: list[str] = []
    trace_polls: list[str] = []

    async def poll_status(current):
        status_polls.append(current.work_unit_id)
        return ProviderObservation(status="running", source_revision=None)

    async def poll_trace(current, checkpoint):
        trace_polls.append(current.work_unit_id)
        assert checkpoint.cursor is None
        return ProviderTraceAdapterResult(
            observation=ProviderTraceObservation(
                schema_version=1,
                adapter_version="analysis-delta-v1",
                source_revision=1,
                next_cursor="cursor-1",
                snapshot_complete=False,
                health="healthy",
                records=(
                    ProviderTraceRecord(
                        source_identity="provider-record-1",
                        record_class="bounded_progress",
                        semantic_code="gene_network.infer_network",
                        status="running",
                        completed=1,
                        total=3,
                    ),
                ),
            ),
            overlap_identities=("provider-record-1",),
        )

    def present(current, observation, record):
        return parse_execution_event_intent_v2(
            {
                "type": "work_unit.progress",
                "status": record.status,
                "source": "provider",
                "span_id": current.parent_span_id,
                "work_unit_id": current.work_unit_id,
                "attempt": record.attempt,
                "summary": {
                    "key": "execution.trace.geneNetwork.inferNetwork",
                    "text": "Infer regulatory network",
                },
                "public_payload": {
                    "phase": record.semantic_code,
                    "completed": record.completed,
                    "total": record.total,
                },
                "idempotency_key": (
                    f"trace:{current.work_unit_id}:"
                    f"{observation.adapter_version}:"
                    f"{record.source_identity}"
                ),
            }
        )

    reconciler = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis": poll_status},
        trace_pollers={"analysis": poll_trace},
        trace_presenter=present,
        trace_poll_interval_seconds=30,
        clock=lambda: now,
    )
    assert asyncio.run(reconciler.reconcile(unit)) is True
    current = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert current.provider_trace_cursor == "cursor-1"
    assert current.provider_trace_revision == 1
    assert current.provider_trace_health == "healthy"

    assert asyncio.run(reconciler.reconcile(current)) is False
    assert status_polls == ["work-provider", "work-provider"]
    assert trace_polls == ["work-provider"]
    page = journal.list_events(unit.execution_id, owner=unit.owner, limit=100)
    assert page is not None
    assert (
        len(
            [
                event
                for event in page.items
                if event.idempotency_key
                and event.idempotency_key.startswith("trace:")
            ]
        )
        == 1
    )


def test_trace_outage_does_not_block_authoritative_status_settlement(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
        ProviderObservation,
        ProviderReconciler,
    )
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceAdapterResult,
        ProviderTraceObservation,
    )

    reservations, journal, work, unit = _seed_provider_work(
        tmp_path / "provider-trace-outage.db"
    )

    async def poll_status(current):
        del current
        return ProviderObservation(status="succeeded", source_revision=4)

    trace_available = False
    clock = [datetime(2026, 8, 23, tzinfo=UTC)]

    async def poll_trace(current, checkpoint):
        del current, checkpoint
        if not trace_available:
            raise RuntimeError("private provider transport detail")
        return ProviderTraceAdapterResult(
            observation=ProviderTraceObservation(
                schema_version=1,
                adapter_version="analysis-delta-v1",
                source_revision=1,
                next_cursor="cursor-recovered",
                snapshot_complete=False,
                health="healthy",
            )
        )

    reconciler = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis": poll_status},
        trace_pollers={"analysis": poll_trace},
        trace_backoff_seconds=60,
        clock=lambda: clock[0],
    )
    assert asyncio.run(reconciler.reconcile(unit)) is True
    current = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert current.status.value == "succeeded"
    assert current.provider_revision == 4
    assert current.provider_trace_health == "degraded"

    page = journal.list_events(unit.execution_id, owner=unit.owner, limit=100)
    assert page is not None
    assert [
        event.type.value
        for event in page.items
        if event.type.value.startswith("tracking.")
    ] == ["tracking.degraded"]

    trace_available = True
    clock[0] = clock[0].replace(minute=2)
    assert asyncio.run(reconciler.reconcile(current)) is False
    recovered = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert recovered.status.value == "succeeded"
    assert recovered.provider_trace_health == "healthy"
    page = journal.list_events(unit.execution_id, owner=unit.owner, limit=100)
    assert page is not None
    assert [
        event.type.value
        for event in page.items
        if event.type.value.startswith("tracking.")
    ] == ["tracking.degraded", "tracking.recovered"]


def test_trace_checkpoint_and_public_facts_commit_atomically(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        ExecutionJournalPublicationFenceError,
    )
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_intent_v2,
    )

    _reservations, journal, work, unit = _seed_provider_work(
        tmp_path / "provider-trace-atomic.db"
    )
    intent = parse_execution_event_intent_v2(
        {
            "type": "work_unit.progress",
            "status": "running",
            "source": "provider",
            "span_id": unit.parent_span_id,
            "work_unit_id": unit.work_unit_id,
            "attempt": 1,
            "summary": {"key": "trace.phase", "text": "Trace phase"},
            "public_payload": {
                "phase": "gene_network.infer_network",
                "completed": 1,
                "total": 2,
            },
            "idempotency_key": "trace:atomic-record-1",
        }
    )

    with pytest.raises(
        ExecutionJournalPublicationFenceError,
        match="provider_trace_revision_conflict",
    ):
        journal.append_provider_trace_batch(
            unit.execution_id,
            owner=unit.owner,
            work_unit_id=unit.work_unit_id,
            expected_work_revision=unit.revision - 1,
            cursor="cursor-1",
            source_revision=1,
            adapter_version="analysis-delta-v1",
            overlap_identities=("record-1",),
            contact_at="2026-08-23T00:00:00+00:00",
            health="healthy",
            intents=(intent,),
        )

    current = work.get_work_unit(
        unit.execution_id, unit.work_unit_id, owner=unit.owner
    )
    assert current.provider_trace_cursor is None
    page = journal.list_events(unit.execution_id, owner=unit.owner, limit=100)
    assert page is not None
    assert all(
        event.idempotency_key != "trace:atomic-record-1"
        for event in page.items
    )


def test_terminal_provider_work_stops_status_and_trace_supervision(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_supervisor_v2 import (
        ExecutionSupervisor,
    )
    from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
        ProviderObservation,
        ProviderReconciler,
    )
    from mcp_server_phytomni.runtime.provider_trace_v2 import (
        ProviderTraceAdapterResult,
        ProviderTraceObservation,
    )

    reservations, journal, work, _unit = _seed_provider_work(
        tmp_path / "provider-trace-terminal-stop.db"
    )
    status_polls: list[str] = []
    trace_polls: list[str] = []

    async def poll_status(current):
        status_polls.append(current.work_unit_id)
        return ProviderObservation(status="succeeded", source_revision=1)

    async def poll_trace(current, checkpoint):
        del checkpoint
        trace_polls.append(current.work_unit_id)
        return ProviderTraceAdapterResult(
            observation=ProviderTraceObservation(
                schema_version=1,
                adapter_version="analysis-delta-v1",
                source_revision=1,
                next_cursor="terminal-cursor",
                snapshot_complete=False,
                health="healthy",
            )
        )

    reconciler = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis": poll_status},
        trace_pollers={"analysis": poll_trace},
    )
    supervisor = ExecutionSupervisor(
        work=work,
        worker_id="provider-terminal-worker",
        handler=reconciler.reconcile,
        due_work_loader=work.list_due_provider_work_units,
    )

    assert asyncio.run(supervisor.run_once()).processed == 1
    assert asyncio.run(supervisor.run_once()).claimed == 0
    assert status_polls == ["work-provider"]
    assert trace_polls == ["work-provider"]
