# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Leased bounded supervisor for durable V2 execution work units."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .execution_work_store_v2 import (
    ExecutionWorkConflictError,
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
    WorkUnitStatus,
)

WorkUnitHandler = Callable[[WorkUnitRecord], Awaitable[object]]
TimeoutHandler = Callable[[WorkUnitRecord], Awaitable[None]]
AuxiliaryRecovery = Callable[[], Awaitable[None]]
DueWorkLoader = Callable[..., tuple[WorkUnitRecord, ...]]


async def run_auxiliary_recovery(
    *,
    name: str,
    recover: AuxiliaryRecovery,
    timeout_seconds: float = 60.0,
) -> None:
    """Run one bounded legacy-store adapter under supervisor ownership.

    Research retains its domain-specific outbox/store and lease rules while
    migration is active. This boundary makes the common Bot supervisor the
    only process-lifecycle scheduler; the adapter remains a delegated domain
    handler and does not copy its recovery decisions.
    """
    if not name or len(name) > 64:
        raise ValueError("bounded recovery name is required")
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise ValueError("invalid auxiliary recovery timeout")
    await asyncio.wait_for(recover(), timeout=timeout_seconds)


@dataclass(frozen=True, slots=True)
class SupervisorRunResult:
    """Bounded outcome of one supervisor scan."""

    discovered: int
    claimed: int
    processed: int
    retried: int
    lease_conflicts: int
    timed_out: int = 0
    budget_exhausted: int = 0


class ExecutionSupervisor:
    """Claim due work and delegate it without embedding Agent decisions."""

    def __init__(
        self,
        *,
        work: SQLiteExecutionWorkRepository,
        worker_id: str,
        handler: WorkUnitHandler,
        timeout_handler: TimeoutHandler | None = None,
        max_concurrency: int = 8,
        lease_seconds: int = 30,
        base_backoff_seconds: int = 1,
        max_backoff_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
        due_work_loader: DueWorkLoader | None = None,
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("bounded worker_id is required")
        if max_concurrency < 1 or max_concurrency > 128:
            raise ValueError("max_concurrency must be between 1 and 128")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if (
            base_backoff_seconds < 1
            or max_backoff_seconds < base_backoff_seconds
        ):
            raise ValueError("invalid supervisor backoff bounds")
        self._work = work
        self._worker_id = worker_id
        self._handler = handler
        self._timeout_handler = timeout_handler
        self._max_concurrency = max_concurrency
        self._lease_seconds = lease_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._due_work_loader = (
            due_work_loader or self._work.list_due_work_units
        )

    async def run_once(self) -> SupervisorRunResult:
        """Process at most one bounded due-work cohort."""
        due = self._due_work_loader(
            now=self._clock(), limit=self._max_concurrency
        )
        claimed: list[WorkUnitRecord] = []
        conflicts = 0
        for unit in due:
            try:
                claimed.append(
                    self._work.claim_lease(
                        unit.execution_id,
                        unit.work_unit_id,
                        owner=unit.owner,
                        worker_id=self._worker_id,
                        lease_seconds=self._lease_seconds,
                        expected_revision=unit.revision,
                    )
                )
            except ExecutionWorkConflictError:
                conflicts += 1
        outcomes = await asyncio.gather(
            *(self._process(unit) for unit in claimed)
        )
        return SupervisorRunResult(
            discovered=len(due),
            claimed=len(claimed),
            processed=sum(outcome == "processed" for outcome in outcomes),
            retried=sum(outcome == "retried" for outcome in outcomes),
            lease_conflicts=conflicts,
            timed_out=sum(outcome == "timed_out" for outcome in outcomes),
            budget_exhausted=sum(
                outcome == "budget_exhausted" for outcome in outcomes
            ),
        )

    async def _process(self, claimed: WorkUnitRecord) -> str:
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_lease(claimed, stop),
            name=f"execution-supervisor-heartbeat:{claimed.work_unit_id}",
        )
        failed = False
        try:
            current = self._work.get_work_unit(
                claimed.execution_id,
                claimed.work_unit_id,
                owner=claimed.owner,
            )
            if self._deadline_exceeded(current):
                current = self._work.update_work_unit_status(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    status=WorkUnitStatus.TIMED_OUT,
                    expected_revision=current.revision,
                )
                if self._timeout_handler is not None:
                    await self._timeout_handler(current)
                outcome = "timed_out"
            else:
                if current.status is WorkUnitStatus.RETRY_SCHEDULED:
                    current = self._work.start_attempt(
                        current.execution_id,
                        current.work_unit_id,
                        owner=current.owner,
                        expected_revision=current.revision,
                    )
                await self._handler(current)
                outcome = "processed"
        except Exception:
            failed = True
            outcome = "failed"
        finally:
            stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

        current = self._work.get_work_unit(
            claimed.execution_id,
            claimed.work_unit_id,
            owner=claimed.owner,
        )
        if current.lease_owner != self._worker_id:
            return outcome
        if failed:
            if current.status in {
                WorkUnitStatus.SUCCEEDED,
                WorkUnitStatus.PARTIAL,
                WorkUnitStatus.FAILED,
                WorkUnitStatus.CANCELLED,
                WorkUnitStatus.TIMED_OUT,
            }:
                self._work.release_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._worker_id,
                    expected_revision=current.revision,
                )
                return "processed"
            if current.attempt >= current.max_attempts:
                current = self._work.update_work_unit_status(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    status=WorkUnitStatus.FAILED,
                    expected_revision=current.revision,
                )
                self._work.release_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._worker_id,
                    expected_revision=current.revision,
                )
                return "budget_exhausted"
            delay = min(
                self._max_backoff_seconds,
                self._base_backoff_seconds
                * (2 ** max(0, current.attempt - 1)),
            )
            self._work.schedule_retry(
                current.execution_id,
                current.work_unit_id,
                owner=current.owner,
                worker_id=self._worker_id,
                next_attempt_at=self._clock() + timedelta(seconds=delay),
                error_code="supervisor_handler_failed",
                expected_revision=current.revision,
            )
            return "retried"
        self._work.release_lease(
            current.execution_id,
            current.work_unit_id,
            owner=current.owner,
            worker_id=self._worker_id,
            expected_revision=current.revision,
        )
        return outcome

    def _deadline_exceeded(self, unit: WorkUnitRecord) -> bool:
        if unit.status is WorkUnitStatus.WAITING_INPUT:
            return False
        deadline = self._work.execution_deadline_at(
            unit.execution_id,
            owner=unit.owner,
        )
        if unit.deadline_at is not None:
            work_deadline = datetime.fromisoformat(
                unit.deadline_at.replace("Z", "+00:00")
            )
            deadline = min(deadline, work_deadline)
        return self._clock() >= deadline

    async def _renew_lease(
        self,
        claimed: WorkUnitRecord,
        stop: asyncio.Event,
    ) -> None:
        interval = max(0.1, self._lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            current = self._work.get_work_unit(
                claimed.execution_id,
                claimed.work_unit_id,
                owner=claimed.owner,
            )
            if current.lease_owner != self._worker_id:
                return
            try:
                self._work.claim_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                    expected_revision=current.revision,
                )
            except ExecutionWorkConflictError:
                return
