# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Serving-process owner for durable Runtime V2 remote reconciliation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict, Unpack, cast

from ..agents.analyst.agent import task_log
from ..public_agent_catalog import (
    provider_trace_agent_slugs,
    public_agent_spec,
)
from ..storage.path_policy import IdFactory
from .execution_drivers_v2 import CANONICAL_DRIVER_TYPES
from .execution_entrypoint_v2 import invoke_public_agent_operation
from .execution_event_flags import execution_log_artifact_enabled
from .execution_journal_store_v2 import (
    ExecutionJournal,
    SQLiteExecutionJournal,
)
from .execution_journal_v2 import ExecutionStatus
from .execution_log_artifact_v2 import SQLiteExecutionLogArtifactStore
from .execution_reservation_v2 import SQLiteExecutionReservationRepository
from .execution_runtime_contracts import DriverOutcome
from .execution_runtime_v2 import ExecutionRuntime
from .execution_supervisor_v2 import ExecutionSupervisor
from .execution_target_store_v2 import SQLiteExecutionTargetStore
from .execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
)
from .gene_network_provider_trace_v2 import (
    present_agent_provider_record,
)
from .provider_reconciliation_v2 import ProviderObservation, ProviderReconciler
from .provider_trace_v2 import (
    FullSnapshotProviderTraceAdapter,
    ProviderTraceAdapterResult,
    ProviderTraceCheckpoint,
    ProviderTraceRecord,
)
from .run_registry import RunRegistry
from .task_reconcile import reconcile_task

_LOGGER = logging.getLogger(__name__)
_TERMINAL_RUN_STATUS = {
    "succeeded": ExecutionStatus.SUCCEEDED,
    "partial": ExecutionStatus.PARTIAL,
    "failed": ExecutionStatus.FAILED,
    "cancelled": ExecutionStatus.CANCELLED,
    "timed_out": ExecutionStatus.TIMED_OUT,
}
_SUPERVISOR_FAILURES: tuple[type[Exception], ...] = (Exception,)

type _RunOnce = Callable[[], Awaitable[Any]]
SettleExecution = Callable[[str, str, str], Awaitable[None]]
_PROVIDER_JOIN_LEASE_SECONDS = 900
_PROVIDER_JOIN_RENEWAL_SECONDS = 60.0


class _ReadyProviderJoinFields(TypedDict):
    reservations: SQLiteExecutionReservationRepository
    journal: ExecutionJournal
    work: SQLiteExecutionWorkRepository
    settle: SettleExecution
    worker_id: str
    lease_seconds: NotRequired[int]
    renewal_interval_seconds: NotRequired[float]
    limit: NotRequired[int]


@dataclass(frozen=True, slots=True)
class _ReadyProviderJoinServices:
    reservations: SQLiteExecutionReservationRepository
    journal: ExecutionJournal
    work: SQLiteExecutionWorkRepository
    settle: SettleExecution


@dataclass(frozen=True, slots=True)
class _ReadyProviderJoinPolicy:
    worker_id: str
    lease_seconds: int
    renewal_interval_seconds: float
    limit: int


async def poll_analysis_task_platform(
    unit: WorkUnitRecord,
) -> ProviderObservation:
    """Reuse the canonical Analyst status seam for one stored provider id."""
    task_id = unit.provider_task_id
    if not task_id:
        raise RuntimeError("provider_identity_not_bound")
    projected = await reconcile_task(task_id)
    live = projected.get("live_status")
    raw_status = (
        live.get("status")
        if isinstance(live, dict)
        else projected.get("status")
    )
    status = _provider_status(raw_status)
    revision = _provider_observation_revision(
        live,
        unit.provider_revision,
        terminal=status in _TERMINAL_RUN_STATUS,
    )
    return ProviderObservation(status=status, source_revision=revision)


async def poll_analysis_task_platform_trace(
    unit: WorkUnitRecord,
    checkpoint: ProviderTraceCheckpoint,
) -> ProviderTraceAdapterResult:
    """Fetch one uncached full snapshot and normalize only structured facts."""
    task_id = unit.provider_task_id
    if not task_id:
        raise RuntimeError("provider_identity_not_bound")
    payload = await task_log(task_id)
    records = payload.get("logs") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise RuntimeError("provider_trace_unavailable")

    def normalize(
        raw: object, source_identity: str, _index: int
    ) -> ProviderTraceRecord | None:
        if not isinstance(raw, dict):
            return None
        candidate = dict(raw)
        candidate.pop("id", None)
        candidate["source_identity"] = source_identity
        try:
            return ProviderTraceRecord.model_validate(candidate)
        except ValueError:
            return None

    adapter = FullSnapshotProviderTraceAdapter(
        adapter_version="analysis-full-v1",
        normalize_record=normalize,
    )
    return adapter.adapt(records, checkpoint=checkpoint)


class ReadyProviderJoinReconciler:
    """Retry the terminal-child/root-settlement crash window."""

    def __init__(self, **fields: Unpack[_ReadyProviderJoinFields]) -> None:
        lease_seconds = fields.get(
            "lease_seconds", _PROVIDER_JOIN_LEASE_SECONDS
        )
        renewal_interval_seconds = fields.get(
            "renewal_interval_seconds", _PROVIDER_JOIN_RENEWAL_SECONDS
        )
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if not 0 < renewal_interval_seconds < lease_seconds:
            raise ValueError("renewal interval must be below lease duration")
        self._services = _ReadyProviderJoinServices(
            reservations=fields["reservations"],
            journal=fields["journal"],
            work=fields["work"],
            settle=fields["settle"],
        )
        self._policy = _ReadyProviderJoinPolicy(
            worker_id=fields["worker_id"],
            lease_seconds=lease_seconds,
            renewal_interval_seconds=renewal_interval_seconds,
            limit=fields.get("limit", 100),
        )

    @property
    def worker_id(self) -> str:
        """Return the bounded provider-join lease owner."""
        return self._policy.worker_id

    async def run_once(self) -> int:
        """Attempt every ready join; failures remain discoverable next scan."""
        settled = 0
        for (
            owner,
            execution_id,
        ) in self._services.work.list_ready_provider_joins(
            limit=self._policy.limit
        ):
            try:
                # The reservation is the authorization boundary.  The event
                # projection can legitimately be absent during startup crash
                # recovery, so it must not prevent a durable join retry.
                self._services.reservations.get(
                    owner=owner,
                    execution_id=execution_id,
                )
                lease_token = IdFactory().new_id(
                    "provider-join-lease", self._policy.worker_id
                )
                if not self._services.work.claim_provider_join_lease(
                    execution_id,
                    owner=owner,
                    lease_token=lease_token,
                    lease_seconds=self._policy.lease_seconds,
                ):
                    continue
                try:
                    await self._settle_with_lease_renewal(
                        owner, execution_id, lease_token
                    )
                finally:
                    self._services.work.release_provider_join_lease(
                        execution_id,
                        owner=owner,
                        lease_token=lease_token,
                    )
            except _SUPERVISOR_FAILURES as exc:
                _LOGGER.warning(
                    "Execution provider join reconciliation failed",
                    extra={
                        "execution_id": execution_id,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            settled += 1
        return settled

    async def _settle_with_lease_renewal(
        self,
        owner: str,
        execution_id: str,
        lease_token: str,
    ) -> None:
        """Keep the unique join claim fenced for the full aggregation."""
        stop_renewal = asyncio.Event()

        async def renew() -> None:
            while not stop_renewal.is_set():
                try:
                    await asyncio.wait_for(
                        stop_renewal.wait(),
                        timeout=self._policy.renewal_interval_seconds,
                    )
                except TimeoutError:
                    if not self._services.work.renew_provider_join_lease(
                        execution_id,
                        owner=owner,
                        lease_token=lease_token,
                        lease_seconds=self._policy.lease_seconds,
                    ):
                        raise RuntimeError(
                            "provider_join_lease_lost"
                        ) from None

        async def settle_once() -> None:
            await self._services.settle(owner, execution_id, lease_token)

        settle_task: asyncio.Task[None] = asyncio.create_task(settle_once())
        renewal_task = asyncio.create_task(renew())
        try:
            done, _pending = await asyncio.wait(
                {settle_task, renewal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal_task in done:
                await renewal_task
            await settle_task
            stop_renewal.set()
            await renewal_task
        finally:
            stop_renewal.set()
            if not settle_task.done():
                settle_task.cancel()
            await asyncio.gather(settle_task, return_exceptions=True)
            await asyncio.gather(renewal_task, return_exceptions=True)


class DomainTerminalReconciler:
    """Mirror domain-owned terminal rows into the canonical V2 journal."""

    def __init__(
        self,
        *,
        reservations: SQLiteExecutionReservationRepository,
        journal: ExecutionJournal,
        work: SQLiteExecutionWorkRepository,
        limit: int = 100,
    ) -> None:
        self._reservations = reservations
        self._journal = journal
        self._work = work
        self._limit = limit

    @property
    def limit(self) -> int:
        """Return the maximum recoverable terminals projected per scan."""
        return self._limit

    async def run_once(self) -> int:
        """Project recoverable domain terminals into the V2 journal."""
        projected = 0
        for record in self._reservations.list_recoverable(limit=self._limit):
            outcome = _domain_terminal_outcome(record.status)
            if outcome is None:
                continue
            spec = public_agent_spec(record.agent_slug)
            if spec is None:
                continue
            runtime = ExecutionRuntime(
                reservations=self._reservations,
                journal=self._journal,
                work=self._work,
                drivers={spec.driver: CANONICAL_DRIVER_TYPES[spec.driver]({})},
                target_store=SQLiteExecutionTargetStore(
                    self._reservations.db_path
                ),
                execution_log_store=(
                    SQLiteExecutionLogArtifactStore(self._reservations.db_path)
                    if execution_log_artifact_enabled()
                    else None
                ),
            )
            projected += int(
                runtime.adopt_domain_terminal(
                    owner=record.owner,
                    execution_id=record.execution_id,
                    outcome=outcome,
                )
            )
        return projected


async def settle_ready_provider_execution(
    db_path: str,
    owner: str,
    execution_id: str,
    *,
    lease_token: str | None = None,
) -> None:
    """Delegate remote aggregation to RunRegistry, then settle Runtime V2."""
    reservations = SQLiteExecutionReservationRepository(db_path)
    reservation = reservations.get(owner=owner, execution_id=execution_id)
    if reservation.run_id is None:
        raise RuntimeError("execution_run_id_required")
    registry = (
        RunRegistry(db_path)
        if lease_token is None
        else RunRegistry(
            db_path,
            expected_provider_join_lease_token=lease_token,
        )
    )

    run = registry.get_run(reservation.run_id, owner=owner)
    if run is None:
        raise RuntimeError("execution_run_unavailable")
    if run.status not in _TERMINAL_RUN_STATUS:
        # Domain reconciliation can perform remote status/artifact I/O.  Do
        # that before claiming the Runtime operation so a transient failure
        # remains discoverable by the next ready-join scan instead of being
        # converted into a terminal Runtime failure.
        run = await registry.reconcile(reservation.run_id, owner=owner)
    if run is None:
        raise RuntimeError("execution_run_unavailable")
    reconciled: dict[str, object] = {
        "status": run.status,
        "result": run.result,
    }
    if lease_token is not None and not SQLiteExecutionWorkRepository(
        db_path
    ).owns_provider_join_lease(
        execution_id,
        owner=owner,
        lease_token=lease_token,
    ):
        raise RuntimeError("provider_join_lease_lost")

    async def project_reconciled_run() -> dict[str, object]:
        return reconciled

    reservation_current = reservations.get(
        owner=owner, execution_id=execution_id
    )
    await invoke_public_agent_operation(
        db_path=db_path,
        owner=owner,
        execution_id=execution_id,
        agent_slug=reservation_current.agent_slug,
        operation="reconcile",
        action_id=f"provider-join:{reservation_current.supervisor_revision}",
        expected_revision=reservation_current.supervisor_revision,
        arguments={"source": "provider_join"},
        transport="supervisor",
        call=project_reconciled_run,
        status_mapper=_run_status,
        expected_provider_join_lease_token=lease_token,
    )


async def run_execution_supervisor_loop(
    *,
    supervisor: object,
    joins: object,
    terminals: object | None = None,
    stop: asyncio.Event,
    poll_seconds: float = 5.0,
) -> None:
    """Poll on a bounded cadence; stream/read traffic is never the trigger."""
    if poll_seconds <= 0 or poll_seconds > 300:
        raise ValueError("invalid supervisor poll interval")
    supervisor_run = _run_once_callable(supervisor)
    join_run = _run_once_callable(joins)
    terminal_run = (
        _run_once_callable(terminals) if terminals is not None else None
    )
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        if stop.is_set():
            return
        await _run_reconciler(
            supervisor_run,
            "Execution work supervisor scan failed",
        )
        await _run_reconciler(
            join_run,
            "Execution join supervisor scan failed",
        )
        if terminal_run is not None:
            await _run_reconciler(
                terminal_run,
                "Execution domain terminal projection failed",
            )


def _run_once_callable(service: object) -> _RunOnce:
    run_once = getattr(service, "run_once", None)
    if not callable(run_once):
        raise TypeError("supervisor service must expose run_once")
    return cast(_RunOnce, run_once)


async def _run_reconciler(run_once: _RunOnce, failure_message: str) -> None:
    try:
        await run_once()
    except _SUPERVISOR_FAILURES as exc:
        _LOGGER.warning(
            failure_message,
            extra={"error_type": type(exc).__name__},
        )


async def run_execution_supervisor_service(
    *,
    db_path: str,
    stop: asyncio.Event,
) -> None:
    """Build canonical repositories/pollers and own them for this process."""
    reservations = SQLiteExecutionReservationRepository(db_path)
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)

    def present_provider_trace(unit, observation, record):
        analysis_span = work.find_span_by_work_unit_id(
            unit.execution_id,
            unit.work_unit_id,
            owner=unit.owner,
        )
        if analysis_span is None:
            return ()
        reservation = reservations.get(
            owner=unit.owner,
            execution_id=unit.execution_id,
        )
        return present_agent_provider_record(
            reservation.agent_slug,
            unit,
            observation,
            record,
            analysis_span_id=analysis_span.span_id,
        )

    providers = ProviderReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis_task_platform": poll_analysis_task_platform},
        trace_pollers={
            "analysis_task_platform": poll_analysis_task_platform_trace
        },
        trace_presenter=present_provider_trace,
        trace_agent_slugs=provider_trace_agent_slugs(),
    )
    supervisor = ExecutionSupervisor(
        work=work,
        worker_id=IdFactory().new_id("worker", "execution-supervisor"),
        handler=providers.reconcile,
        max_concurrency=8,
        due_work_loader=work.list_due_provider_work_units,
    )

    async def settle(owner: str, execution_id: str, lease_token: str) -> None:
        await settle_ready_provider_execution(
            db_path,
            owner,
            execution_id,
            lease_token=lease_token,
        )

    joins = ReadyProviderJoinReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
        settle=settle,
        worker_id=IdFactory().new_id("worker", "provider-join"),
    )
    terminals = DomainTerminalReconciler(
        reservations=reservations,
        journal=journal,
        work=work,
    )
    await run_execution_supervisor_loop(
        supervisor=supervisor,
        joins=joins,
        terminals=terminals,
        stop=stop,
    )


def _domain_terminal_outcome(
    status: ExecutionStatus,
) -> DriverOutcome | None:
    if status is ExecutionStatus.FAILED:
        return DriverOutcome.failed(code="domain_execution_failed")
    if status is ExecutionStatus.CANCELLED:
        return DriverOutcome(
            status=status,
            cancellation_outcome="confirmed",
        )
    if status in {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.PARTIAL,
        ExecutionStatus.TIMED_OUT,
    }:
        return DriverOutcome(status=status)
    return None


def _provider_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"submitted", "pending", "queued", "acknowledged"}:
        return "pending"
    if normalized in {"running", "in_progress"}:
        return "running"
    if normalized in {"succeeded", "success", "completed", "done"}:
        return "succeeded"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"timed_out", "timeout"}:
        return "timed_out"
    raise RuntimeError("unsupported_provider_status")


def _provider_observation_revision(
    live: object,
    current_revision: int,
    *,
    terminal: bool,
) -> int | None:
    """Coalesce provider liveness to one public fact per 30-second bucket."""
    if not isinstance(live, dict):
        return None
    elapsed: list[int] = []
    direct = live.get("actual_running_time")
    if isinstance(direct, int) and not isinstance(direct, bool):
        elapsed.append(direct)
    runtime_rows = live.get("task_runtime_info")
    if isinstance(runtime_rows, list):
        for row in runtime_rows:
            if not isinstance(row, dict):
                continue
            value = row.get("actual_running_time")
            if isinstance(value, int) and not isinstance(value, bool):
                elapsed.append(value)
            children = row.get("sub_tasks")
            if isinstance(children, list):
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_value = child.get("actual_running_time")
                    if isinstance(child_value, int) and not isinstance(
                        child_value, bool
                    ):
                        elapsed.append(child_value)
    if not elapsed:
        return current_revision + 1 if terminal else None
    bucket_revision = max(elapsed) // 30 + 1
    if terminal:
        return max(current_revision + 1, bucket_revision)
    return bucket_revision if bucket_revision > current_revision else None


def _run_status(value: Any) -> ExecutionStatus:
    candidate = (
        value[0] if isinstance(value, tuple) and len(value) == 2 else value
    )
    status = candidate.get("status") if isinstance(candidate, dict) else None
    normalized = str(status or "").strip().lower()
    if normalized in _TERMINAL_RUN_STATUS:
        return _TERMINAL_RUN_STATUS[normalized]
    if normalized in {"waiting", "waiting_input", "input_required"}:
        return ExecutionStatus.WAITING_INPUT
    return ExecutionStatus.RUNNING


__all__ = [
    "DomainTerminalReconciler",
    "ReadyProviderJoinReconciler",
    "poll_analysis_task_platform",
    "poll_analysis_task_platform_trace",
    "run_execution_supervisor_loop",
    "run_execution_supervisor_service",
    "settle_ready_provider_execution",
]
