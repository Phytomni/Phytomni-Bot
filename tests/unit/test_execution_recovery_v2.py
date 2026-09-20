# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Orphan recovery routes durable shapes through canonical owners."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from tests.support.execution_supervisor_v2 import bind_acknowledged_provider

from mcp_server_phytomni.runtime.execution_drivers_v2 import RemoteTaskDriver
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    parse_execution_event_intent_v2,
)
from mcp_server_phytomni.runtime.execution_recovery_v2 import (
    ExecutionRecoveryCoordinator,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import ExecutionRuntime
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitSpec,
)


def _runtime_fixture(db_path: Path):

    path = str(db_path)
    reservations = SQLiteExecutionReservationRepository(path)
    journal = SQLiteExecutionJournal(path)
    work = SQLiteExecutionWorkRepository(path)
    calls: list[str] = []

    async def handler(context, command, services):
        del context, services
        calls.append(str(command.arguments.get("work_unit_id", "start")))
        return DriverOutcome.running()

    runtime = ExecutionRuntime(
        reservations=reservations,
        journal=journal,
        work=work,
        drivers={
            "remote_task": RemoteTaskDriver(
                {
                    DriverOperation.START: handler,
                    DriverOperation.RECOVER: handler,
                }
            )
        },
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-recovery",
            fingerprint_version=1,
            fingerprint="a" * 64,
            command=ExecutionCommand(
                agent_slug="analyst", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    reservation = reservations.get(owner="alice", execution_id="turn-recovery")
    unit = work.create_work_unit(
        WorkUnitSpec(
            owner="alice",
            execution_id="turn-recovery",
            work_unit_id="work-orphan",
            parent_span_id=reservation.root_span_id,
            operation_key="provider.submit",
            driver="remote_task",
            join_policy="best_effort",
            max_attempts=3,
        )
    )
    return runtime, reservations, journal, work, unit, calls


def test_pending_submission_checkpoint_and_join_delegate_to_driver_recovery(
    tmp_path: Path,
) -> None:
    """Verify pending submission checkpoint and join delegate to driver
    recovery."""

    runtime, reservations, journal, _work, unit, calls = _runtime_fixture(
        tmp_path / "driver-orphans.db"
    )

    @dataclass(eq=False)
    class Providers:
        """Provider bundle that delegates checkpoint and join recovery."""

        async def reconcile(self, current):
            """Reconcile the configured provider state."""
            raise AssertionError(current)

    recovery = ExecutionRecoveryCoordinator(
        runtime=runtime,
        reservations=reservations,
        journal=journal,
        providers=Providers(),
    )
    result = asyncio.run(recovery.recover_work_unit(unit))
    assert result.driver_recovered == 1
    assert calls == ["start", "work-orphan"]


def test_acknowledged_remote_work_delegates_to_provider_reconciler(
    tmp_path: Path,
) -> None:
    """Verify acknowledged remote work delegates to provider reconciler."""

    runtime, reservations, journal, work, unit, calls = _runtime_fixture(
        tmp_path / "remote-orphan.db"
    )
    unit = bind_acknowledged_provider(
        work,
        unit,
        provider_task_id="task-private",
    )
    reconciled: list[str] = []

    @dataclass(eq=False)
    class Providers:
        """Provider bundle for acknowledged-work reconciliation."""

        async def reconcile(self, current):
            """Reconcile the configured provider state."""
            reconciled.append(current.work_unit_id)
            return True

    recovery = ExecutionRecoveryCoordinator(
        runtime=runtime,
        reservations=reservations,
        journal=journal,
        providers=Providers(),
    )
    result = asyncio.run(recovery.recover_work_unit(unit))
    assert result.provider_reconciled == 1
    assert reconciled == ["work-orphan"]
    assert calls == ["start"]


def test_terminal_journal_crash_window_settles_without_rerunning_agent(
    tmp_path: Path,
) -> None:
    """Verify terminal journal crash window settles without rerunning agent."""

    runtime, reservations, journal, _work, unit, calls = _runtime_fixture(
        tmp_path / "terminal-orphan.db"
    )
    journal.append(
        unit.execution_id,
        owner=unit.owner,
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.succeeded",
                "status": "succeeded",
                "source": "supervisor",
                "span_id": unit.parent_span_id,
                "attempt": 1,
                "summary": {
                    "key": "execution.succeeded",
                    "text": "Execution succeeded",
                },
                "public_payload": {},
                "idempotency_key": "terminal-crash-window",
            }
        ),
    )

    @dataclass(eq=False)
    class Providers:
        """Provider bundle used to recover a terminal journal checkpoint."""

        async def reconcile(self, current):
            """Reconcile the configured provider state."""
            raise AssertionError(current)

    recovery = ExecutionRecoveryCoordinator(
        runtime=runtime,
        reservations=reservations,
        journal=journal,
        providers=Providers(),
    )
    assert recovery.settle_pending_terminal_projections() == 1
    assert recovery.settle_pending_terminal_projections() == 0
    record = reservations.get(owner=unit.owner, execution_id=unit.execution_id)
    assert record.status.value == "succeeded"
    assert calls == ["start"]
