# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Leases, concurrency, stealing, and retry behavior for the V2 supervisor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.support.execution_supervisor_v2 import mark_work_unit_succeeded

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import WorkUnitStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_supervisor_v2 import (
    ExecutionSupervisor,
    run_auxiliary_recovery,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
    WorkUnitSpec,
)


def _seed(db_path: Path, *, count: int, clock=None):

    path = str(db_path)
    reservations = SQLiteExecutionReservationRepository(path, clock=clock)
    work = SQLiteExecutionWorkRepository(path, clock=clock)
    journal = SQLiteExecutionJournal(path, clock=clock)
    del journal
    reservations.reserve(
        owner="alice",
        execution_id="turn-supervisor",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=ExecutionCommand(
            agent_slug="analyst", arguments={"query": "rice"}
        ),
    )
    root = reservations.get(owner="alice", execution_id="turn-supervisor")
    work.create_span(
        SpanSpec(
            owner="alice",
            execution_id="turn-supervisor",
            span_id=root.root_span_id,
            kind="agent",
            label_key="agent.analyst",
        )
    )
    for index in range(count):
        work.create_work_unit(
            WorkUnitSpec(
                owner="alice",
                execution_id="turn-supervisor",
                work_unit_id=f"work-{index}",
                parent_span_id=root.root_span_id,
                operation_key=f"provider.work-{index}",
                driver="remote_task",
                max_attempts=3,
            )
        )
    return work


def test_supervisors_contend_for_one_lease_and_process_once(
    tmp_path: Path,
) -> None:
    """Verify supervisors contend for one lease and process once."""

    work = _seed(tmp_path / "contention.db", count=1)
    calls: list[str] = []

    async def handler(unit):
        calls.append(unit.work_unit_id)
        mark_work_unit_succeeded(work, unit)

    async def exercise():
        first = ExecutionSupervisor(
            work=work, worker_id="worker-a", handler=handler, max_concurrency=1
        )
        second = ExecutionSupervisor(
            work=work, worker_id="worker-b", handler=handler, max_concurrency=1
        )
        return await asyncio.gather(first.run_once(), second.run_once())

    results = asyncio.run(exercise())
    assert calls == ["work-0"]
    assert sum(result.claimed for result in results) == 1
    assert (
        work.get_work_unit(
            "turn-supervisor", "work-0", owner="alice"
        ).lease_owner
        is None
    )


def test_supervisor_steals_expired_lease_and_bounds_concurrency(
    tmp_path: Path,
) -> None:
    """Verify supervisor steals expired lease and bounds concurrency."""

    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock():
        return now[0]

    work = _seed(tmp_path / "steal.db", count=3, clock=clock)
    first = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    work.claim_lease(
        first.execution_id,
        first.work_unit_id,
        owner=first.owner,
        worker_id="dead-worker",
        lease_seconds=5,
        expected_revision=first.revision,
    )
    now[0] += timedelta(seconds=6)
    active = 0
    peak = 0

    async def handler(_unit):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1

    result = asyncio.run(
        ExecutionSupervisor(
            work=work,
            worker_id="live-worker",
            handler=handler,
            max_concurrency=2,
            clock=clock,
        ).run_once()
    )
    assert result.claimed == 2
    assert peak <= 2
    assert "work-0" in {
        unit.work_unit_id
        for unit in work.list_due_work_units(now=now[0], limit=10)
    }


def test_supervisor_failure_schedules_bounded_retry_without_exception_text(
    tmp_path: Path,
) -> None:
    """Verify supervisor failure schedules bounded retry without exception
    text."""

    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock():
        return now[0]

    work = _seed(tmp_path / "retry.db", count=1, clock=clock)

    async def handler(unit):
        del unit
        raise RuntimeError("password=placeholder-provider-error")

    result = asyncio.run(
        ExecutionSupervisor(
            work=work,
            worker_id="worker-retry",
            handler=handler,
            base_backoff_seconds=2,
            max_backoff_seconds=8,
            clock=clock,
        ).run_once()
    )
    unit = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    assert result.retried == 1
    assert unit.status.value == "retry_scheduled"
    assert unit.next_attempt_at == (now[0] + timedelta(seconds=2)).isoformat()
    assert unit.last_error_code == "supervisor_handler_failed"
    assert "placeholder-provider-error" not in str(unit.model_dump())


def test_supervisor_renews_lease_while_handler_is_running(
    tmp_path: Path,
) -> None:
    """Verify supervisor renews lease while handler is running."""

    work = _seed(tmp_path / "heartbeat.db", count=1)
    renewed: list[bool] = []

    async def handler(unit):
        initial_expiry = unit.lease_expires_at
        await asyncio.sleep(0.45)
        current = work.get_work_unit(
            unit.execution_id, unit.work_unit_id, owner=unit.owner
        )
        renewed.append(
            current.lease_expires_at is not None
            and initial_expiry is not None
            and current.lease_expires_at > initial_expiry
        )

    asyncio.run(
        ExecutionSupervisor(
            work=work,
            worker_id="worker-heartbeat",
            handler=handler,
            max_concurrency=1,
            lease_seconds=1,
        ).run_once()
    )
    assert renewed == [True]


def test_supervisor_advances_attempts_and_exhausts_retry_budget(
    tmp_path: Path,
) -> None:
    """Verify supervisor advances attempts and exhausts retry budget."""

    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock():
        return now[0]

    work = _seed(tmp_path / "budget.db", count=1, clock=clock)
    unit = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    # This Driver declares two total attempts.
    with __import__("sqlite3").connect(work.db_path) as connection:
        connection.execute(
            "UPDATE execution_work_units SET max_attempts = 2 "
            "WHERE work_unit_id = ?",
            (unit.work_unit_id,),
        )

    async def fail(current):
        del current
        raise RuntimeError("provider outage")

    supervisor = ExecutionSupervisor(
        work=work,
        worker_id="worker-budget",
        handler=fail,
        base_backoff_seconds=1,
        clock=clock,
    )
    first = asyncio.run(supervisor.run_once())
    assert first.retried == 1
    now[0] += timedelta(seconds=1)
    second = asyncio.run(supervisor.run_once())
    current = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    assert second.budget_exhausted == 1
    assert current.attempt == 2
    assert current.status.value == "failed"


def test_supervisor_times_out_expired_work_before_calling_driver(
    tmp_path: Path,
) -> None:
    """Verify supervisor times out expired work before calling driver."""

    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock():
        return now[0]

    work = _seed(tmp_path / "deadline.db", count=1, clock=clock)
    with __import__("sqlite3").connect(work.db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_deadline_at = ? "
            "WHERE execution_id = ?",
            ((now[0] - timedelta(seconds=1)).isoformat(), "turn-supervisor"),
        )
    called: list[str] = []
    settled: list[str] = []

    async def handler(unit):
        called.append(unit.work_unit_id)

    async def settle_timeout(unit):
        settled.append(unit.work_unit_id)

    result = asyncio.run(
        ExecutionSupervisor(
            work=work,
            worker_id="worker-deadline",
            handler=handler,
            timeout_handler=settle_timeout,
            clock=clock,
        ).run_once()
    )
    current = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    assert result.timed_out == 1
    assert not called
    assert settled == ["work-0"]
    assert current.status.value == "timed_out"


def test_new_worker_recovers_retry_after_process_restart(
    tmp_path: Path,
) -> None:
    """Verify new worker recovers retry after process restart."""

    now = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock():
        return now[0]

    work = _seed(tmp_path / "restart.db", count=1, clock=clock)

    async def outage(unit):
        del unit
        raise RuntimeError("provider unavailable")

    first = ExecutionSupervisor(
        work=work,
        worker_id="worker-before-restart",
        handler=outage,
        base_backoff_seconds=1,
        clock=clock,
    )
    assert asyncio.run(first.run_once()).retried == 1
    now[0] += timedelta(seconds=1)
    recovered: list[int] = []

    async def recover(unit):
        recovered.append(unit.attempt)
        current = work.get_work_unit(
            unit.execution_id, unit.work_unit_id, owner=unit.owner
        )
        work.update_work_unit_status(
            current.execution_id,
            current.work_unit_id,
            owner=current.owner,
            status=WorkUnitStatus.SUCCEEDED,
            expected_revision=current.revision,
        )

    second = ExecutionSupervisor(
        work=work,
        worker_id="worker-after-restart",
        handler=recover,
        clock=clock,
    )
    assert asyncio.run(second.run_once()).processed == 1
    assert recovered == [2]


def test_terminal_handler_race_never_reopens_work_as_retry(
    tmp_path: Path,
) -> None:
    """Verify terminal handler race never reopens work as retry."""

    work = _seed(tmp_path / "terminal-race.db", count=1)

    async def terminal_then_late_error(unit):
        current = work.get_work_unit(
            unit.execution_id, unit.work_unit_id, owner=unit.owner
        )
        work.update_work_unit_status(
            current.execution_id,
            current.work_unit_id,
            owner=current.owner,
            status=WorkUnitStatus.SUCCEEDED,
            expected_revision=current.revision,
        )
        raise RuntimeError("late callback raced terminal settlement")

    result = asyncio.run(
        ExecutionSupervisor(
            work=work,
            worker_id="worker-terminal-race",
            handler=terminal_then_late_error,
        ).run_once()
    )
    current = work.get_work_unit("turn-supervisor", "work-0", owner="alice")
    assert result.processed == 1
    assert result.retried == 0
    assert current.status.value == "succeeded"
    assert current.lease_owner is None


def test_research_legacy_store_recovery_is_a_bounded_supervisor_adapter() -> (
    None
):
    """Verify research legacy store recovery is a bounded supervisor
    adapter."""

    calls: list[str] = []

    async def recover():
        calls.append("research-domain-recovery")

    asyncio.run(
        run_auxiliary_recovery(
            name="research",
            recover=recover,
            timeout_seconds=1,
        )
    )
    assert calls == ["research-domain-recovery"]
    app_support = (
        Path(__file__).parents[2]
        / "src/mcp_server_phytomni/api/app_support.py"
    ).read_text(encoding="utf-8")
    assert "run_auxiliary_recovery(" in app_support
    assert "await recover_registered_startup()" not in app_support
