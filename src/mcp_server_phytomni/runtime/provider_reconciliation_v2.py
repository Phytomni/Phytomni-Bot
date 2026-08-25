# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable provider callback and polling reconciliation for Runtime V2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..public_agent_catalog import public_agent_spec
from .execution_instrumentation_v2 import ExecutionBoundary
from .execution_journal_store_v2 import (
    ExecutionJournal,
    SQLiteExecutionJournal,
)
from .execution_journal_v2 import (
    ExecutionEventIntentV2,
    parse_execution_event_intent_v2,
)
from .execution_reservation_v2 import SQLiteExecutionReservationRepository
from .execution_runtime_contracts import ExecutionContext, ExecutionServices
from .execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
)
from .provider_instrumentation_v2 import record_provider_observation
from .provider_trace_v2 import (
    ProviderTraceAdapterResult,
    ProviderTraceCheckpoint,
    ProviderTraceObservation,
    ProviderTraceRecord,
)


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """Finite provider status obtained from a callback or recovery poll."""

    status: str
    source_revision: int | None = None

    def __post_init__(self) -> None:
        normalized = self.status.strip().lower()
        if normalized not in {
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
        }:
            raise ValueError("unsupported provider observation")
        if self.source_revision is not None and self.source_revision < 0:
            raise ValueError("provider revision must be non-negative")
        object.__setattr__(self, "status", normalized)


ProviderPoller = Callable[[WorkUnitRecord], Awaitable[ProviderObservation]]
ProviderTracePoller = Callable[
    [WorkUnitRecord, ProviderTraceCheckpoint],
    Awaitable[ProviderTraceAdapterResult],
]
ProviderTracePresenter = Callable[
    [WorkUnitRecord, ProviderTraceObservation, ProviderTraceRecord],
    ExecutionEventIntentV2 | Sequence[ExecutionEventIntentV2] | None,
]


class ProviderReconciler:
    """Fold callback and polling observations through one durable writer."""

    def __init__(
        self,
        *,
        reservations: SQLiteExecutionReservationRepository,
        journal: ExecutionJournal,
        work: SQLiteExecutionWorkRepository,
        pollers: Mapping[str, ProviderPoller],
        trace_pollers: Mapping[str, ProviderTracePoller] | None = None,
        trace_presenter: ProviderTracePresenter | None = None,
        trace_agent_slugs: frozenset[str] | None = None,
        trace_poll_interval_seconds: float = 15.0,
        trace_backoff_seconds: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reservations = reservations
        self._journal = journal
        self._work = work
        self._pollers = dict(pollers)
        self._trace_pollers = dict(trace_pollers or {})
        self._trace_presenter = trace_presenter
        self._trace_agent_slugs = trace_agent_slugs
        if not 1 <= trace_poll_interval_seconds <= 300:
            raise ValueError("invalid provider trace poll interval")
        if not trace_poll_interval_seconds <= trace_backoff_seconds <= 900:
            raise ValueError("invalid provider trace backoff")
        self._trace_poll_interval_seconds = trace_poll_interval_seconds
        self._trace_backoff_seconds = trace_backoff_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def reconcile(self, unit: WorkUnitRecord) -> bool:
        """Supervisor handler: poll only from stored provider correlation."""
        if unit.provider_kind is None or unit.provider_task_id is None:
            raise RuntimeError("provider_identity_not_bound")
        poller = self._pollers.get(unit.provider_kind)
        if poller is None:
            raise RuntimeError("provider_poller_unavailable")
        observation = await poller(unit)
        current = self._work.get_work_unit(
            unit.execution_id,
            unit.work_unit_id,
            owner=unit.owner,
        )
        status_changed = self._commit(current, observation)
        current = self._work.get_work_unit(
            unit.execution_id,
            unit.work_unit_id,
            owner=unit.owner,
        )
        await self._reconcile_trace(current)
        return status_changed

    def observe_callback(
        self,
        *,
        owner: str,
        execution_id: str,
        provider_kind: str,
        provider_task_id: str,
        observation: ProviderObservation,
    ) -> bool:
        """Callback acceleration using the same revision/idempotency rules."""
        unit = self._work.find_work_unit_by_provider_task_id(
            execution_id,
            provider_task_id,
            owner=owner,
            provider_kind=provider_kind,
        )
        if unit is None:
            return False
        return self._commit(unit, observation)

    def _commit(
        self,
        unit: WorkUnitRecord,
        observation: ProviderObservation,
    ) -> bool:
        if unit.provider_kind is None or unit.provider_task_id is None:
            return False
        reservation = self._reservations.get(
            owner=unit.owner,
            execution_id=unit.execution_id,
        )
        spec = public_agent_spec(reservation.agent_slug)
        if spec is None:
            raise RuntimeError("execution_agent_unavailable")
        deadline = datetime.fromisoformat(reservation.deadline_at)
        context = ExecutionContext(
            owner_ref=reservation.owner,
            execution_id=reservation.execution_id,
            fingerprint_version=reservation.fingerprint_version,
            fingerprint=reservation.fingerprint,
            agent=spec,
            root_span_id=reservation.root_span_id,
            current_span_id=unit.parent_span_id,
            transport="supervisor",
            run_id=reservation.run_id,
            deadline_at=deadline,
        )
        boundary = ExecutionBoundary(
            context=context,
            services=ExecutionServices(
                journal=self._journal,
                reservations=self._reservations,
                work=self._work,
                clock=self._clock,
            ),
        )
        return record_provider_observation(
            boundary=boundary,
            record=unit,
            provider_kind=unit.provider_kind,
            provider_task_id=unit.provider_task_id,
            source_revision=observation.source_revision,
            observed_status=observation.status,
        )

    async def _reconcile_trace(self, unit: WorkUnitRecord) -> None:
        if self._trace_agent_slugs is not None:
            reservation = self._reservations.get(
                owner=unit.owner, execution_id=unit.execution_id
            )
            if reservation.agent_slug not in self._trace_agent_slugs:
                return
        provider_kind = unit.provider_kind
        if provider_kind is None:
            return
        poller = self._trace_pollers.get(provider_kind)
        if poller is None or not self._trace_due(unit):
            return
        checkpoint = ProviderTraceCheckpoint(
            adapter_version=unit.provider_trace_adapter_version,
            cursor=unit.provider_trace_cursor,
            source_revision=unit.provider_trace_revision,
            overlap_identities=unit.provider_trace_overlap_identities,
        )
        contact_at = self._clock().isoformat()
        try:
            result = await poller(unit, checkpoint)
        except Exception:
            if not isinstance(self._journal, SQLiteExecutionJournal):
                raise RuntimeError(
                    "atomic_provider_trace_journal_required"
                ) from None
            health_intents = (
                (self._trace_health_intent(unit, degraded=True),)
                if unit.provider_trace_health == "healthy"
                else ()
            )
            self._journal.append_provider_trace_batch(
                unit.execution_id,
                owner=unit.owner,
                work_unit_id=unit.work_unit_id,
                expected_work_revision=unit.revision,
                cursor=unit.provider_trace_cursor,
                source_revision=unit.provider_trace_revision,
                adapter_version=(
                    unit.provider_trace_adapter_version or "unavailable-v1"
                ),
                overlap_identities=(unit.provider_trace_overlap_identities),
                contact_at=contact_at,
                health="degraded",
                intents=health_intents,
            )
            return

        intents: list[ExecutionEventIntentV2] = []
        if self._trace_presenter is not None:
            for record in result.observation.records:
                presented = self._trace_presenter(
                    unit, result.observation, record
                )
                if isinstance(presented, ExecutionEventIntentV2):
                    intents.append(presented)
                elif presented is not None:
                    intents.extend(presented)
        source_revision = (
            result.observation.source_revision
            if result.observation.source_revision is not None
            else unit.provider_trace_revision + 1
        )
        result_degraded = result.observation.health != "healthy"
        was_degraded = unit.provider_trace_health != "healthy"
        if result_degraded and not was_degraded:
            intents.insert(0, self._trace_health_intent(unit, degraded=True))
        elif not result_degraded and was_degraded:
            intents.insert(0, self._trace_health_intent(unit, degraded=False))
        if not isinstance(self._journal, SQLiteExecutionJournal):
            raise RuntimeError("atomic_provider_trace_journal_required")
        self._journal.append_provider_trace_batch(
            unit.execution_id,
            owner=unit.owner,
            work_unit_id=unit.work_unit_id,
            expected_work_revision=unit.revision,
            cursor=result.observation.next_cursor,
            source_revision=source_revision,
            adapter_version=result.observation.adapter_version,
            overlap_identities=result.overlap_identities,
            contact_at=contact_at,
            health=result.observation.health,
            intents=tuple(intents),
        )

    def _trace_health_intent(
        self, unit: WorkUnitRecord, *, degraded: bool
    ) -> ExecutionEventIntentV2:
        span = self._work.find_span_by_work_unit_id(
            unit.execution_id,
            unit.work_unit_id,
            owner=unit.owner,
        )
        span_id = span.span_id if span is not None else unit.parent_span_id
        parent_span_id = span.parent_span_id if span is not None else None
        state = "degraded" if degraded else "recovered"
        return parse_execution_event_intent_v2(
            {
                "type": f"tracking.{state}",
                "status": "degraded" if degraded else "running",
                "source": "provider",
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "work_unit_id": unit.work_unit_id,
                "attempt": unit.attempt,
                "summary": {
                    "key": f"execution.trace.{state}",
                    "text": (
                        "Detailed work trace is temporarily unavailable"
                        if degraded
                        else "Detailed work trace recovered"
                    ),
                },
                "public_payload": {
                    "health": "degraded" if degraded else "healthy",
                    "code": (
                        "provider_trace_unavailable" if degraded else None
                    ),
                    "retryable": degraded,
                },
                "idempotency_key": (
                    f"trace-health:{unit.work_unit_id}:"
                    f"{unit.provider_trace_revision}:{state}"
                ),
            }
        )

    def _trace_due(self, unit: WorkUnitRecord) -> bool:
        if unit.provider_trace_contact_at is None:
            return True
        previous = datetime.fromisoformat(
            unit.provider_trace_contact_at.replace("Z", "+00:00")
        )
        interval = (
            self._trace_poll_interval_seconds
            if unit.provider_trace_health == "healthy"
            else self._trace_backoff_seconds
        )
        return (self._clock() - previous).total_seconds() >= interval
