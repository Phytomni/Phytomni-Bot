# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Restart-safe routing for orphaned Runtime V2 execution work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .execution_journal_store_v2 import ExecutionJournal
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_v2 import SQLiteExecutionReservationRepository
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)
from .execution_runtime_v2 import ExecutionRuntime
from .execution_work_store_v2 import WorkUnitRecord


@dataclass(init=False, repr=False, eq=False, match_args=False)
class ProviderReconcilerProtocol(Protocol):
    """Minimal provider-recovery dependency used by the coordinator."""

    async def reconcile(self, unit: WorkUnitRecord, /) -> bool:
        """Reconcile one durable provider work unit."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class RecoveryRunResult:
    """Bounded recovery facts suitable for supervisor metrics."""

    terminal_settled: int = 0
    provider_reconciled: int = 0
    driver_recovered: int = 0


class ExecutionRecoveryCoordinator:
    """Route durable orphan shapes without copying Agent business rules."""

    def __init__(
        self,
        *,
        runtime: ExecutionRuntime,
        reservations: SQLiteExecutionReservationRepository,
        journal: ExecutionJournal,
        providers: ProviderReconcilerProtocol,
    ) -> None:
        self._runtime = runtime
        self._reservations = reservations
        self._journal = journal
        self._providers = providers

    def settle_pending_terminal_projections(self, *, limit: int = 100) -> int:
        """Finish the journal-before-reservation crash window idempotently."""
        settled = 0
        for record in self._reservations.list_recoverable(limit=limit):
            projection = self._journal.get_projection(
                record.execution_id,
                owner=record.owner,
            )
            if projection.terminal is None:
                continue
            status = ExecutionStatus(projection.terminal.status)
            outcome = _terminal_outcome(status)
            authority = TerminalSettlementAuthority(
                owner_ref=record.owner,
                execution_id=record.execution_id,
                expected_revision=record.supervisor_revision,
                actor="supervisor",
                issued_at=datetime.now(UTC),
            )
            settled += int(
                self._reservations.settle_terminal(authority, outcome)
            )
        return settled

    async def recover_work_unit(
        self, unit: WorkUnitRecord
    ) -> RecoveryRunResult:
        """Supervisor handler for remote, checkpoint, and join orphans."""
        if (
            unit.provider_kind is not None
            and unit.provider_task_id is not None
        ):
            applied = await self._providers.reconcile(unit)
            return RecoveryRunResult(provider_reconciled=int(applied))
        record = self._reservations.get(
            owner=unit.owner,
            execution_id=unit.execution_id,
        )
        outcome = await self._runtime.recover(
            owner=unit.owner,
            execution_id=unit.execution_id,
            command=ExecutionCommand(
                agent_slug=record.agent_slug,
                arguments={"work_unit_id": unit.work_unit_id},
                action_id=(
                    f"recover:{unit.work_unit_id}:"
                    f"{record.supervisor_revision}"
                ),
                expected_revision=record.supervisor_revision,
            ),
            transport="supervisor",
        )
        del outcome
        return RecoveryRunResult(driver_recovered=1)


def _terminal_outcome(status: ExecutionStatus) -> DriverOutcome:
    if status is ExecutionStatus.FAILED:
        return DriverOutcome.failed(code="recovered_terminal_failure")
    return DriverOutcome(
        status=status,
        cancellation_outcome=(
            "confirmed" if status is ExecutionStatus.CANCELLED else None
        ),
    )
