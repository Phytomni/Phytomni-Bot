# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""The serving supervisor observes remote work and retries durable joins."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.execution_supervisor_v2 import (
    create_root_span,
    mark_work_unit_succeeded,
    seed_running_remote_execution,
    set_provider_join_lease,
)

from mcp_server_phytomni.runtime import execution_supervisor_service_v2
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import WorkUnitStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_supervisor_service_v2 import (
    DomainTerminalReconciler,
    ReadyProviderJoinReconciler,
    run_execution_supervisor_loop,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
    WorkUnitSpec,
)
from mcp_server_phytomni.runtime.provider_trace_v2 import (
    ProviderTraceCheckpoint,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_analysis_provider_poller_projects_live_status_and_revision(
    monkeypatch,
) -> None:
    """Verify analysis provider poller projects live status and revision."""

    async def reconcile(_task_id: str):
        return {
            "status": "RUNNING",
            "live_status": {
                "status": "RUNNING",
                "task_runtime_info": [{"actual_running_time": 95}],
            },
        }

    monkeypatch.setattr(
        execution_supervisor_service_v2, "reconcile_task", reconcile
    )
    unit = WorkUnitRecord(
        owner="alice",
        execution_id="turn-provider",
        work_unit_id="work-provider",
        parent_span_id="span-root",
        operation_key="provider.analysis.submit",
        driver="provider",
        max_attempts=3,
        status=WorkUnitStatus.ACKNOWLEDGED,
        provider_kind="analysis_task_platform",
        provider_task_id="task-1",
        provider_revision=1,
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )

    observed = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform(unit)
    )

    assert observed.status == "running"
    assert observed.source_revision == 4

    # Repeated five-second scans inside the same 30-second provider bucket do
    # not create duplicate public liveness facts.
    observed_again = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform(
            unit.model_copy(update={"provider_revision": 4})
        )
    )
    assert observed_again.source_revision is None


def test_analysis_provider_poller_forces_terminal_revision_after_old_liveness(
    monkeypatch,
) -> None:
    """Verify analysis provider poller forces terminal revision after old
    liveness."""

    async def reconcile(_task_id: str):
        return {
            "status": "SUCCEEDED",
            "live_status": {
                "status": "SUCCEEDED",
                "task_runtime_info": [{"actual_running_time": 95}],
            },
        }

    monkeypatch.setattr(
        execution_supervisor_service_v2, "reconcile_task", reconcile
    )
    unit = WorkUnitRecord(
        owner="alice",
        execution_id="turn-provider",
        work_unit_id="work-provider",
        parent_span_id="span-root",
        operation_key="provider.analysis.submit",
        driver="provider",
        max_attempts=3,
        status=WorkUnitStatus.RUNNING,
        provider_kind="analysis_task_platform",
        provider_task_id="task-1",
        provider_revision=130,
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
    )

    observed = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform(unit)
    )

    assert observed.status == "succeeded"
    assert observed.source_revision == 131


def test_analysis_trace_poller_accepts_only_structured_finite_records(
    monkeypatch,
) -> None:
    """Verify analysis trace poller accepts only structured finite records."""

    async def fetch_log(_task_id: str, **_kwargs):
        return {
            "count": 2,
            "logs": [
                {
                    "id": "public-structured-1",
                    "record_class": "semantic_phase",
                    "semantic_code": "gene_network.prepare_inputs",
                    "status": "succeeded",
                    "attempt": 1,
                },
                {
                    "collect_time": "2026-08-23T00:00:00Z",
                    "content": "private console output",
                },
            ],
            "log_storage_link": "https://private.invalid/log",
        }

    monkeypatch.setattr(execution_supervisor_service_v2, "task_log", fetch_log)
    now = datetime.now(UTC).isoformat()
    unit = WorkUnitRecord(
        owner="alice",
        execution_id="turn-provider",
        work_unit_id="work-provider",
        parent_span_id="span-root",
        operation_key="remote.analysis",
        driver="provider",
        status=WorkUnitStatus.RUNNING,
        provider_kind="analysis_task_platform",
        provider_task_id="task-1",
        created_at=now,
        updated_at=now,
    )
    result = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform_trace(
            unit, ProviderTraceCheckpoint()
        )
    )

    assert len(result.observation.records) == 1
    assert result.observation.records[0].semantic_code == (
        "gene_network.prepare_inputs"
    )
    assert result.observation.health == "healthy"
    assert "private console output" not in str(result.model_dump())


def test_analysis_trace_poller_never_uses_frozen_compatibility_cache(
    monkeypatch,
) -> None:
    """Verify analysis trace poller never uses frozen compatibility cache."""

    calls = 0

    async def fetch_log(_task_id: str, **_kwargs):
        nonlocal calls
        calls += 1
        records = [
            {
                "id": "record-1",
                "record_class": "semantic_phase",
                "semantic_code": "gene_network.prepare_inputs",
                "status": "succeeded",
            }
        ]
        if calls == 2:
            records.append(
                {
                    "id": "record-2",
                    "record_class": "semantic_tool",
                    "semantic_code": "gene_network.infer_network",
                    "status": "running",
                }
            )
        return {"logs": records}

    monkeypatch.setattr(execution_supervisor_service_v2, "task_log", fetch_log)
    now = datetime.now(UTC).isoformat()
    unit = WorkUnitRecord(
        owner="alice",
        execution_id="turn-provider",
        work_unit_id="work-provider",
        parent_span_id="span-root",
        operation_key="remote.analysis",
        driver="provider",
        status=WorkUnitStatus.RUNNING,
        provider_kind="analysis_task_platform",
        provider_task_id="task-1",
        created_at=now,
        updated_at=now,
    )
    first = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform_trace(
            unit, ProviderTraceCheckpoint()
        )
    )
    second = asyncio.run(
        execution_supervisor_service_v2.poll_analysis_task_platform_trace(
            unit, ProviderTraceCheckpoint.from_result(first)
        )
    )

    assert calls == 2
    assert [
        record.source_identity for record in second.observation.records
    ] == ["provider:record-2"]


def test_supervisor_loop_runs_work_and_join_recovery_until_stopped() -> None:
    """Verify supervisor loop runs work and join recovery until stopped."""

    calls: list[str] = []
    stop = asyncio.Event()

    @dataclass(eq=False)
    class Supervisor:
        """Supervisor double recording work-loop invocations."""

        async def run_once(self):
            """Run once for this test scenario."""
            calls.append("work")

    @dataclass(eq=False)
    class Joins:
        """Join reconciler recording recovery-loop invocations."""

        async def run_once(self):
            """Run once for this test scenario."""
            calls.append("join")
            stop.set()

    asyncio.run(
        run_execution_supervisor_loop(
            supervisor=Supervisor(),
            joins=Joins(),
            stop=stop,
            poll_seconds=0.001,
        )
    )

    assert calls == ["work", "join"]


def test_serving_supervisor_uses_provider_only_due_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify serving supervisor uses provider only due work."""

    captured: dict[str, object] = {}

    @dataclass(eq=False)
    class CapturingSupervisor:
        """Supervisor capturing provider work selected for service."""

        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    async def capture_loop(**kwargs) -> None:
        captured["loop_supervisor"] = kwargs["supervisor"]

    monkeypatch.setattr(
        execution_supervisor_service_v2,
        "ExecutionSupervisor",
        CapturingSupervisor,
    )
    monkeypatch.setattr(
        execution_supervisor_service_v2,
        "run_execution_supervisor_loop",
        capture_loop,
    )

    asyncio.run(
        execution_supervisor_service_v2.run_execution_supervisor_service(
            db_path=str(tmp_path / "serving-supervisor.db"),
            stop=asyncio.Event(),
        )
    )

    loader = captured["due_work_loader"]
    assert callable(loader)
    assert getattr(loader, "__name__", None) == "list_due_provider_work_units"
    assert captured["loop_supervisor"].__class__ is CapturingSupervisor


def test_domain_terminal_is_projected_into_the_one_runtime_journal(
    tmp_path: Path,
) -> None:
    """Verify domain terminal is projected into the one runtime journal."""

    db_path = str(tmp_path / "domain-terminal.db")

    seed_running_remote_execution(
        db_path,
        execution_id="turn-domain-terminal",
        agent_slug="research",
        arguments={"query": "rice"},
    )
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET status = 'succeeded' "
            "WHERE user_id = ? AND execution_id = ?",
            ("alice", "turn-domain-terminal"),
        )
        connection.commit()

    reconciler = DomainTerminalReconciler(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=SQLiteExecutionJournal(db_path),
        work=SQLiteExecutionWorkRepository(db_path),
    )
    assert asyncio.run(reconciler.run_once()) == 1
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-domain-terminal", owner="alice"
    )
    assert projection.terminal is not None
    assert projection.terminal.status == "succeeded"
    assert not SQLiteExecutionReservationRepository(db_path).list_recoverable(
        limit=10
    )


def test_ready_provider_join_is_retried_until_execution_is_terminal(
    tmp_path: Path,
) -> None:
    """Verify ready provider join is retried until execution is terminal."""

    db_path = str(tmp_path / "ready-join.db")
    reservations = SQLiteExecutionReservationRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="turn-ready-join",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=ExecutionCommand(
            agent_slug="design", arguments={"query": "rice"}
        ),
    )
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    create_root_span(
        work,
        reservation,
        label_key="agent.design",
    )
    for index in range(2):
        unit = work.create_work_unit(
            WorkUnitSpec(
                owner="alice",
                execution_id=reservation.execution_id,
                work_unit_id=f"work-{index}",
                parent_span_id=reservation.root_span_id,
                operation_key="provider.analysis.submit",
                driver="provider",
            )
        )
        unit = work.bind_provider(
            unit.execution_id,
            unit.work_unit_id,
            owner=unit.owner,
            provider_kind="analysis_task_platform",
            provider_task_id=f"task-{index}",
            provider_revision=1,
            expected_revision=unit.revision,
        )
        mark_work_unit_succeeded(work, unit)

    attempts: list[str] = []

    async def settle(owner: str, execution_id: str, _lease_token: str) -> None:
        attempts.append(f"{owner}:{execution_id}")

    joins = ReadyProviderJoinReconciler(
        settle=settle,
        work=work,
        journal=journal,
        reservations=reservations,
        worker_id="join-worker-a",
    )
    assert asyncio.run(joins.run_once()) == 1
    assert asyncio.run(joins.run_once()) == 1
    assert attempts == [
        "alice:turn-ready-join",
        "alice:turn-ready-join",
    ]


def test_ready_provider_join_is_single_flight_across_supervisors(
    tmp_path: Path,
) -> None:
    """Only one supervisor may execute terminal domain aggregation."""

    db_path = str(tmp_path / "ready-join-single-flight.db")
    reservations = SQLiteExecutionReservationRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="turn-ready-join-single-flight",
        fingerprint_version=1,
        fingerprint="b" * 64,
        command=ExecutionCommand(
            agent_slug="network", arguments={"query": "network"}
        ),
    )
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    create_root_span(
        work,
        reservation,
        label_key="agent.network",
    )
    unit = work.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id=reservation.execution_id,
            work_unit_id="work-provider",
            parent_span_id=reservation.root_span_id,
            operation_key="provider.analysis.submit",
            driver="provider",
        )
    )
    unit = work.bind_provider(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        provider_kind="analysis_task_platform",
        provider_task_id="task-provider",
        provider_revision=1,
        expected_revision=unit.revision,
    )
    work.update_work_unit_status(
        unit.execution_id,
        unit.work_unit_id,
        owner=unit.owner,
        status=WorkUnitStatus.SUCCEEDED,
        expected_revision=unit.revision,
    )

    settle_started = asyncio.Event()
    release_settle = asyncio.Event()
    calls: list[str] = []

    async def settle(owner: str, execution_id: str, _lease_token: str) -> None:
        calls.append(f"{owner}:{execution_id}")
        settle_started.set()
        await release_settle.wait()

    first = ReadyProviderJoinReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        settle=settle,
        worker_id="join-worker-shared",
        lease_seconds=1,
        renewal_interval_seconds=0.1,
    )
    second = ReadyProviderJoinReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        settle=settle,
        worker_id="join-worker-shared",
        lease_seconds=1,
        renewal_interval_seconds=0.1,
    )

    async def exercise() -> tuple[int, int]:
        first_scan = asyncio.create_task(first.run_once())
        await asyncio.wait_for(settle_started.wait(), timeout=1)
        # Cross the original lease TTL. A unique claim token plus renewal
        # must still prevent another scan, even with the same worker label.
        await asyncio.sleep(1.2)
        second_count = await second.run_once()
        release_settle.set()
        return await first_scan, second_count

    assert asyncio.run(exercise()) == (1, 0)
    assert calls == ["alice:turn-ready-join-single-flight"]


def test_stale_provider_join_token_cannot_commit_domain_terminal(
    tmp_path: Path,
) -> None:
    """A superseded join cannot win the domain terminal CAS."""

    db_path = str(tmp_path / "provider-join-fencing.db")
    stale = RunRegistry(
        db_path, expected_provider_join_lease_token="lease-old"
    )
    stale.create_run(RunSpec("run-fenced", "alice", "network", "local"))
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_provider_join_lease_owner = ?, "
            "execution_provider_join_lease_expires_at = ? "
            "WHERE run_id = ? AND user_id = ?",
            (
                "lease-old",
                (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "run-fenced",
                "alice",
            ),
        )
        connection.commit()
    stale_current = stale.get_run("run-fenced", owner="alice")
    assert stale_current is not None

    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_provider_join_lease_owner = ?, "
            "execution_provider_join_lease_expires_at = ? "
            "WHERE run_id = ? AND user_id = ?",
            (
                "lease-new",
                (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "run-fenced",
                "alice",
            ),
        )
        connection.commit()

    stale_winner = getattr(stale, "_settle_terminal")(
        stale_current,
        RunOutcome("succeeded", {"formatted": {"answer": "stale"}}),
    )
    assert stale_winner is not None
    assert stale_winner.status == "running"

    current = RunRegistry(
        db_path, expected_provider_join_lease_token="lease-new"
    )
    current_winner = getattr(current, "_settle_terminal")(
        stale_current,
        RunOutcome("succeeded", {"formatted": {"answer": "current"}}),
    )
    assert current_winner is not None
    assert current_winner.status == "succeeded"
    assert current_winner.result == {"formatted": {"answer": "current"}}


def test_stale_provider_join_token_cannot_claim_runtime_operation(
    tmp_path: Path,
) -> None:
    """Runtime revision CAS fences a token superseded after preflight."""

    db_path = str(tmp_path / "provider-join-runtime-fencing.db")

    baseline = seed_running_remote_execution(
        db_path,
        execution_id="turn-runtime-fenced",
        agent_slug="network",
        arguments={"query": "network"},
    )
    set_provider_join_lease(
        db_path,
        execution_id="turn-runtime-fenced",
        lease_token="lease-new",
    )

    command = ExecutionCommand(
        agent_slug="network",
        arguments={"source": "provider_join"},
        action_id=f"provider-join:{baseline.supervisor_revision}",
        expected_revision=baseline.supervisor_revision,
    )
    stale = SQLiteExecutionReservationRepository(
        db_path, expected_provider_join_lease_token="lease-old"
    )
    with pytest.raises(ExecutionReservationConflictError, match="stale"):
        stale.claim_operation(
            owner="alice",
            execution_id="turn-runtime-fenced",
            operation_id=str(command.action_id),
            operation="reconcile",
            expected_revision=baseline.supervisor_revision,
            command=command,
        )

    unchanged = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice", execution_id="turn-runtime-fenced"
    )
    assert unchanged.supervisor_revision == baseline.supervisor_revision
    current = SQLiteExecutionReservationRepository(
        db_path, expected_provider_join_lease_token="lease-new"
    ).claim_operation(
        owner="alice",
        execution_id="turn-runtime-fenced",
        operation_id=str(command.action_id),
        operation="reconcile",
        expected_revision=baseline.supervisor_revision,
        command=command,
    )
    assert current.claimed is True


def test_expired_provider_join_token_cannot_renew_or_claim_runtime(
    tmp_path: Path,
) -> None:
    """An expired token cannot revive itself before another owner arrives."""

    db_path = str(tmp_path / "provider-join-expired.db")

    baseline = seed_running_remote_execution(
        db_path,
        execution_id="turn-expired",
        agent_slug="network",
        arguments={"query": "network"},
    )
    set_provider_join_lease(
        db_path,
        execution_id="turn-expired",
        lease_token="lease-expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert not SQLiteExecutionWorkRepository(
        db_path
    ).renew_provider_join_lease(
        "turn-expired",
        owner="alice",
        lease_token="lease-expired",
        lease_seconds=60,
    )
    command = ExecutionCommand(
        agent_slug="network",
        arguments={"source": "provider_join"},
        action_id=f"provider-join:{baseline.supervisor_revision}",
        expected_revision=baseline.supervisor_revision,
    )
    expired = SQLiteExecutionReservationRepository(
        db_path, expected_provider_join_lease_token="lease-expired"
    )
    with pytest.raises(ExecutionReservationConflictError, match="stale"):
        expired.claim_operation(
            owner="alice",
            execution_id="turn-expired",
            operation_id=str(command.action_id),
            operation="reconcile",
            expected_revision=baseline.supervisor_revision,
            command=command,
        )
