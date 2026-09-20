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
from typing import NotRequired, TypedDict, Unpack

from .async_utils import wait_for_stop
from .execution_work_status_v2 import TERMINAL_WORK_UNIT_STATUSES
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
_HANDLER_FAILURES: tuple[type[Exception], ...] = (Exception,)
_SUPERVISOR_OPTION_NAMES = frozenset(
    {
        "timeout_handler",
        "max_concurrency",
        "lease_seconds",
        "base_backoff_seconds",
        "max_backoff_seconds",
        "clock",
        "due_work_loader",
    }
)


class SupervisorOptions(TypedDict):
    """Optional policy and dependency overrides for a supervisor."""

    timeout_handler: NotRequired[TimeoutHandler | None]
    max_concurrency: NotRequired[int]
    lease_seconds: NotRequired[int]
    base_backoff_seconds: NotRequired[int]
    max_backoff_seconds: NotRequired[int]
    clock: NotRequired[Callable[[], datetime] | None]
    due_work_loader: NotRequired[DueWorkLoader | None]


@dataclass(frozen=True, slots=True)
class _SupervisorPolicy:
    """Validated bounded-concurrency and retry settings."""

    max_concurrency: int = 8
    lease_seconds: int = 30
    base_backoff_seconds: int = 1
    max_backoff_seconds: int = 60


@dataclass(frozen=True, slots=True)
class _SupervisorServices:
    """Injected store, handlers, worker identity, and clock."""

    work: SQLiteExecutionWorkRepository
    worker_id: str
    handler: WorkUnitHandler
    timeout_handler: TimeoutHandler | None
    clock: Callable[[], datetime]
    due_work_loader: DueWorkLoader


def _validate_supervisor_options(options: SupervisorOptions) -> None:
    unexpected = set(options).difference(_SUPERVISOR_OPTION_NAMES)
    if unexpected:
        name = min(unexpected)
        raise TypeError(
            "ExecutionSupervisor() got an unexpected keyword argument "
            f"'{name}'"
        )


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
        **options: Unpack[SupervisorOptions],
    ) -> None:
        _validate_supervisor_options(options)
        policy = _SupervisorPolicy(
            max_concurrency=options.get("max_concurrency", 8),
            lease_seconds=options.get("lease_seconds", 30),
            base_backoff_seconds=options.get("base_backoff_seconds", 1),
            max_backoff_seconds=options.get("max_backoff_seconds", 60),
        )
        if not worker_id or len(worker_id) > 128:
            raise ValueError("bounded worker_id is required")
        if policy.max_concurrency < 1 or policy.max_concurrency > 128:
            raise ValueError("max_concurrency must be between 1 and 128")
        if policy.lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if (
            policy.base_backoff_seconds < 1
            or policy.max_backoff_seconds < policy.base_backoff_seconds
        ):
            raise ValueError("invalid supervisor backoff bounds")
        self._policy = policy
        self._services = _SupervisorServices(
            work=work,
            worker_id=worker_id,
            handler=handler,
            timeout_handler=options.get("timeout_handler"),
            clock=options.get("clock") or (lambda: datetime.now(UTC)),
            due_work_loader=(
                options.get("due_work_loader") or work.list_due_work_units
            ),
        )

    @property
    def worker_id(self) -> str:
        """Return the bounded lease-owner identity."""
        return self._services.worker_id

    async def run_once(self) -> SupervisorRunResult:
        """Process at most one bounded due-work cohort."""
        due = self._services.due_work_loader(
            now=self._services.clock(),
            limit=self._policy.max_concurrency,
        )
        claimed: list[WorkUnitRecord] = []
        conflicts = 0
        for unit in due:
            try:
                claimed.append(
                    self._services.work.claim_lease(
                        unit.execution_id,
                        unit.work_unit_id,
                        owner=unit.owner,
                        worker_id=self._services.worker_id,
                        lease_seconds=self._policy.lease_seconds,
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
            current = self._services.work.get_work_unit(
                claimed.execution_id,
                claimed.work_unit_id,
                owner=claimed.owner,
            )
            if self._deadline_exceeded(current):
                current = self._services.work.update_work_unit_status(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    status=WorkUnitStatus.TIMED_OUT,
                    expected_revision=current.revision,
                )
                if self._services.timeout_handler is not None:
                    await self._services.timeout_handler(current)
                outcome = "timed_out"
            else:
                if current.status is WorkUnitStatus.RETRY_SCHEDULED:
                    current = self._services.work.start_attempt(
                        current.execution_id,
                        current.work_unit_id,
                        owner=current.owner,
                        expected_revision=current.revision,
                    )
                await self._services.handler(current)
                outcome = "processed"
        except _HANDLER_FAILURES:
            failed = True
            outcome = "failed"
        finally:
            stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

        current = self._services.work.get_work_unit(
            claimed.execution_id,
            claimed.work_unit_id,
            owner=claimed.owner,
        )
        if current.lease_owner != self._services.worker_id:
            return outcome
        if failed:
            if current.status in TERMINAL_WORK_UNIT_STATUSES:
                self._services.work.release_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._services.worker_id,
                    expected_revision=current.revision,
                )
                return "processed"
            if current.attempt >= current.max_attempts:
                current = self._services.work.update_work_unit_status(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    status=WorkUnitStatus.FAILED,
                    expected_revision=current.revision,
                )
                self._services.work.release_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._services.worker_id,
                    expected_revision=current.revision,
                )
                return "budget_exhausted"
            delay = min(
                self._policy.max_backoff_seconds,
                self._policy.base_backoff_seconds
                * (2 ** max(0, current.attempt - 1)),
            )
            self._services.work.schedule_retry(
                current.execution_id,
                current.work_unit_id,
                owner=current.owner,
                worker_id=self._services.worker_id,
                next_attempt_at=self._services.clock()
                + timedelta(seconds=delay),
                error_code="supervisor_handler_failed",
                expected_revision=current.revision,
            )
            return "retried"
        self._services.work.release_lease(
            current.execution_id,
            current.work_unit_id,
            owner=current.owner,
            worker_id=self._services.worker_id,
            expected_revision=current.revision,
        )
        return outcome

    def _deadline_exceeded(self, unit: WorkUnitRecord) -> bool:
        if unit.status is WorkUnitStatus.WAITING_INPUT:
            return False
        deadline = self._services.work.execution_deadline_at(
            unit.execution_id,
            owner=unit.owner,
        )
        if unit.deadline_at is not None:
            work_deadline = datetime.fromisoformat(
                unit.deadline_at.replace("Z", "+00:00")
            )
            deadline = min(deadline, work_deadline)
        return self._services.clock() >= deadline

    async def _renew_lease(
        self,
        claimed: WorkUnitRecord,
        stop: asyncio.Event,
    ) -> None:
        interval = max(0.1, self._policy.lease_seconds / 3)
        while True:
            if await wait_for_stop(stop, timeout_seconds=interval):
                return
            current = self._services.work.get_work_unit(
                claimed.execution_id,
                claimed.work_unit_id,
                owner=claimed.owner,
            )
            if current.lease_owner != self._services.worker_id:
                return
            try:
                self._services.work.claim_lease(
                    current.execution_id,
                    current.work_unit_id,
                    owner=current.owner,
                    worker_id=self._services.worker_id,
                    lease_seconds=self._policy.lease_seconds,
                    expected_revision=current.revision,
                )
            except ExecutionWorkConflictError:
                return
