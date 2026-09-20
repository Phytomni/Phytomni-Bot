# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical catalog-driven execution Runtime for public Agents."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict, Unpack

from ..public_agent_catalog import Driver, PublicAgentSpec, public_agent_spec
from .execution_instrumentation_v2 import bind_execution_boundary
from .execution_journal_store_v2 import (
    ExecutionJournal,
    ExecutionJournalPublicationFenceError,
)
from .execution_journal_v2 import (
    EventStatus,
    ExecutionEventType,
    ExecutionEventV2,
    ExecutionStatus,
    SpanStatus,
    TrackingHealth,
    parse_execution_event_intent_v2,
)
from .execution_log_artifact_v2 import (
    ExecutionLogArtifactStore,
    build_execution_log_document,
)
from .execution_reservation_support_v2 import CanonicalReservationKwargs
from .execution_reservation_v2 import (
    ExecutionReservationConflictError,
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from .execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    ExecutionDriver,
    ExecutionRuntimeError,
    ExecutionServices,
)
from .execution_runtime_support_v2 import (
    RuntimeAppendFields,
    RuntimeEventPublication,
    artifact_public_payload,
    build_artifact_publication,
    build_artifact_target_binding,
    build_execution_log_target_binding,
    build_message_publications,
    deserialize_driver_outcome,
    input_resolution_outcome,
    public_action_surface_id,
    reservation_deadline_exceeded,
    reservation_outcome,
    serialize_driver_outcome,
)
from .execution_target_store_v2 import (
    ExecutionTargetBindingFenceError,
    ExecutionTargetStore,
)
from .execution_work_store_v2 import SpanSpec, SQLiteExecutionWorkRepository


class _ExecutionRuntimeFields(TypedDict):
    reservations: SQLiteExecutionReservationRepository
    journal: ExecutionJournal
    work: SQLiteExecutionWorkRepository
    drivers: Mapping[Driver, ExecutionDriver]
    target_store: NotRequired[ExecutionTargetStore | None]
    execution_log_store: NotRequired[ExecutionLogArtifactStore | None]
    clock: NotRequired[Callable[[], datetime] | None]


class _ExecutionStartFields(CanonicalReservationKwargs):
    reservation_command: NotRequired[ExecutionCommand | None]
    transport: str


def build_execution_context(
    record: ExecutionReservationRecord,
    spec: PublicAgentSpec,
    transport: str,
    *,
    current_span_id: str | None = None,
) -> ExecutionContext:
    """Build one execution context from its durable reservation identity."""
    deadline = datetime.fromisoformat(
        record.deadline_at.replace("Z", "+00:00")
    )
    return ExecutionContext(
        owner_ref=record.owner,
        execution_id=record.execution_id,
        fingerprint_version=record.fingerprint_version,
        fingerprint=record.fingerprint,
        agent=spec,
        root_span_id=record.root_span_id,
        current_span_id=(
            record.root_span_id if current_span_id is None else current_span_id
        ),
        transport=transport,
        run_id=record.run_id,
        deadline_at=deadline,
    )


def build_root_span_spec(
    record: ExecutionReservationRecord,
    spec: PublicAgentSpec,
) -> SpanSpec:
    """Build the canonical root Agent span, including its join policy."""
    return SpanSpec(
        owner=record.owner,
        execution_id=record.execution_id,
        span_id=record.root_span_id,
        kind="agent",
        label_key=f"agent.{spec.slug}",
        join_policy=None if spec.join == "not_applicable" else spec.join,
    )


class ExecutionRuntime:
    """Own admission, dispatch, and settlement around business Drivers."""

    def __init__(self, **fields: Unpack[_ExecutionRuntimeFields]) -> None:
        self._reservations = fields["reservations"]
        self._journal = fields["journal"]
        self._work = fields["work"]
        self._drivers = dict(fields["drivers"])
        self._target_store = fields.get("target_store")
        self._execution_log_store = fields.get("execution_log_store")
        clock = fields.get("clock")
        self._clock = clock or (lambda: datetime.now(UTC))

    async def start(
        self,
        **fields: Unpack[_ExecutionStartFields],
    ) -> DriverOutcome:
        """Reserve once while dispatching only prepared business arguments."""
        command = fields["command"]
        spec = public_agent_spec(command.agent_slug)
        if spec is None:
            raise ExecutionRuntimeError("unknown_public_agent")
        canonical_command = fields.get("reservation_command") or command
        if canonical_command.agent_slug != command.agent_slug:
            raise ExecutionRuntimeError("execution_identity_conflict")
        record = self._reservations.reserve(
            owner=fields["owner"],
            execution_id=fields["execution_id"],
            fingerprint_version=fields["fingerprint_version"],
            fingerprint=fields["fingerprint"],
            command=canonical_command,
        )
        if not self._reservations.claim_start(
            owner=fields["owner"],
            execution_id=fields["execution_id"],
        ):
            return reservation_outcome(
                self._reservations.get(
                    owner=fields["owner"],
                    execution_id=fields["execution_id"],
                )
            )

        context = build_execution_context(record, spec, fields["transport"])
        root_span = self._work.create_span(build_root_span_spec(record, spec))
        self._append(
            context,
            event_type=ExecutionEventType.EXECUTION_ADMITTED,
            status=EventStatus.ADMITTED,
            payload={},
            summary_key="execution.admitted",
            summary_text="Execution admitted",
        )
        self._append(
            context,
            event_type=ExecutionEventType.EXECUTION_STARTED,
            status=EventStatus.RUNNING,
            payload={},
            summary_key="execution.started",
            summary_text="Execution started",
        )
        self._append(
            context,
            event_type=ExecutionEventType.SPAN_STARTED,
            status=EventStatus.RUNNING,
            payload={"phase": spec.slug},
            summary_key=f"agent.{spec.slug}.started",
            summary_text="Agent started",
        )
        root_span = self._work.update_span_status(
            fields["execution_id"],
            record.root_span_id,
            owner=fields["owner"],
            status=SpanStatus.RUNNING,
            expected_revision=root_span.revision,
        )
        self._reservations.mark_running(
            owner=fields["owner"],
            execution_id=fields["execution_id"],
        )

        outcome = await self._execute_driver(
            DriverOperation.START,
            context,
            command,
        )

        self._publish_outcome(
            context,
            outcome,
            command=command,
            operation_token="start",
        )
        if not outcome.terminal:
            self._record_nonterminal(
                context,
                root_span.revision,
                outcome,
                operation_token="start",
            )
            return outcome
        self._settle_terminal(context, root_span.revision, outcome)
        return outcome

    def adopt_domain_terminal(
        self,
        *,
        owner: str,
        execution_id: str,
        outcome: DriverOutcome,
        transport: str = "supervisor",
    ) -> bool:
        """Project an already-committed domain terminal into Runtime V2.

        Domain coordinators keep their existing business decisions and tables;
        this bridge only closes the one public Runtime root after their shared
        ``runs`` row has reached a terminal state.
        """
        if not outcome.terminal:
            raise ValueError("terminal outcome required")
        record = self._reservations.get(owner=owner, execution_id=execution_id)
        projection = self._journal.get_projection(execution_id, owner=owner)
        if projection.terminal is not None:
            return False
        spec = public_agent_spec(record.agent_slug)
        if spec is None:
            raise ExecutionRuntimeError("unknown_public_agent")
        context = build_execution_context(record, spec, transport)
        root_span = self._work.get_span(
            execution_id,
            record.root_span_id,
            owner=owner,
        )
        self._settle_terminal(context, root_span.revision, outcome)
        return True

    async def resume(
        self,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
        transport: str,
    ) -> DriverOutcome:
        """Apply a checked input action and resume the same execution."""
        return await self._operate(
            DriverOperation.RESUME,
            owner=owner,
            execution_id=execution_id,
            command=command,
            transport=transport,
        )

    async def cancel(
        self,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
        transport: str,
    ) -> DriverOutcome:
        """Request cancellation without coupling it to stream closure."""
        return await self._operate(
            DriverOperation.CANCEL,
            owner=owner,
            execution_id=execution_id,
            command=command,
            transport=transport,
        )

    async def recover(
        self,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
        transport: str = "supervisor",
    ) -> DriverOutcome:
        """Recover Driver state under an idempotent supervisor action."""
        return await self._operate(
            DriverOperation.RECOVER,
            owner=owner,
            execution_id=execution_id,
            command=command,
            transport=transport,
        )

    async def reconcile(
        self,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
        transport: str = "supervisor",
    ) -> DriverOutcome:
        """Fold a provider observation through the canonical Driver."""
        return await self._operate(
            DriverOperation.RECONCILE,
            owner=owner,
            execution_id=execution_id,
            command=command,
            transport=transport,
        )

    @staticmethod
    def _operation_spec(
        operation: DriverOperation,
        record: ExecutionReservationRecord,
        command: ExecutionCommand,
    ) -> PublicAgentSpec:
        """Validate that an operation targets the reserved public Agent."""
        spec = public_agent_spec(record.agent_slug)
        if spec is None or command.agent_slug != record.agent_slug:
            raise ExecutionRuntimeError("execution_agent_mismatch")
        if operation is DriverOperation.RESUME and spec.resume == "none":
            raise ExecutionRuntimeError("resume_unsupported")
        return spec

    def _claim_operation(
        self,
        operation: DriverOperation,
        context: ExecutionContext,
        command: ExecutionCommand,
    ) -> DriverOutcome | None:
        """Claim an operation, returning its replayed outcome when present."""
        assert command.action_id is not None
        assert command.expected_revision is not None
        try:
            claim = self._reservations.claim_operation(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                operation_id=command.action_id,
                operation=operation.value,
                expected_revision=command.expected_revision,
                command=command,
            )
        except ExecutionReservationConflictError as exc:
            if operation is DriverOperation.RESUME:
                self._append_input_action(
                    context,
                    command=command,
                    event_type=ExecutionEventType.INPUT_ACTION_REJECTED,
                    outcome=(
                        "stale_revision"
                        if str(exc) == "stale_revision"
                        else "action_conflict"
                    ),
                )
            raise
        if claim.claimed:
            return None
        if operation is DriverOperation.RESUME:
            self._append_input_action(
                context,
                command=command,
                event_type=ExecutionEventType.INPUT_ACTION_REJECTED,
                outcome="duplicate_action",
            )
        if claim.outcome_json is not None:
            return deserialize_driver_outcome(claim.outcome_json)
        return reservation_outcome(
            self._reservations.get(
                owner=context.owner_ref,
                execution_id=context.execution_id,
            )
        )

    async def _operate(
        self,
        operation: DriverOperation,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
        transport: str,
    ) -> DriverOutcome:
        if command.action_id is None or command.expected_revision is None:
            raise ExecutionRuntimeError("operation_identity_required")
        record = self._reservations.get(owner=owner, execution_id=execution_id)
        spec = self._operation_spec(operation, record, command)
        context = build_execution_context(record, spec, transport)
        replayed = self._claim_operation(operation, context, command)
        if replayed is not None:
            return replayed

        record = self._reservations.get(owner=owner, execution_id=execution_id)
        context = build_execution_context(record, spec, transport)
        if operation is DriverOperation.RESUME:
            self._append_input_action(
                context,
                command=command,
                event_type=ExecutionEventType.INPUT_ACTION_CLAIMED,
                outcome="claimed",
            )
        root_span = self._work.get_span(
            execution_id,
            record.root_span_id,
            owner=owner,
        )
        operation_token = f"{operation.value}:{command.action_id}"
        if reservation_deadline_exceeded(record, self._clock()):
            self._reservations.require_provider_join_lease(
                owner=owner, execution_id=execution_id
            )
            outcome = DriverOutcome(status=ExecutionStatus.TIMED_OUT)
            self._settle_terminal(context, root_span.revision, outcome)
            self._complete_operation(context, command.action_id, outcome)
            return outcome

        if operation is DriverOperation.RESUME:
            self._append(
                context,
                event_type=ExecutionEventType.EXECUTION_RESUMED,
                status=EventStatus.RUNNING,
                payload={},
                summary_key="execution.resumed",
                summary_text="Execution resumed",
                operation_token=operation_token,
            )
            self._append(
                context,
                event_type=ExecutionEventType.SPAN_RESUMED,
                status=EventStatus.RUNNING,
                payload={"phase": spec.slug},
                summary_key=f"agent.{spec.slug}.resumed",
                summary_text="Agent resumed",
                operation_token=operation_token,
            )
            root_span = self._work.update_span_status(
                execution_id,
                record.root_span_id,
                owner=owner,
                status=SpanStatus.RUNNING,
                expected_revision=root_span.revision,
            )
        elif operation is DriverOperation.CANCEL:
            self._append(
                context,
                event_type=ExecutionEventType.EXECUTION_CANCELLATION_REQUESTED,
                status=EventStatus.CANCELLATION_REQUESTED,
                payload={"outcome": "requested"},
                summary_key="execution.cancellation_requested",
                summary_text="Cancellation requested",
                operation_token=operation_token,
            )

        outcome = await self._execute_driver(operation, context, command)
        self._reservations.require_provider_join_lease(
            owner=owner, execution_id=execution_id
        )
        self._publish_outcome(
            context,
            outcome,
            command=command,
            operation_token=f"{operation.value}:{command.action_id}",
        )
        if operation is DriverOperation.RESUME:
            self._append_input_action(
                context,
                command=command,
                event_type=ExecutionEventType.INPUT_RESOLVED,
                outcome=input_resolution_outcome(command, outcome),
            )
        if outcome.terminal:
            self._settle_terminal(context, root_span.revision, outcome)
        else:
            self._record_nonterminal(
                context,
                root_span.revision,
                outcome,
                operation_token=operation_token,
            )
        self._complete_operation(context, command.action_id, outcome)
        return outcome

    def _publish_outcome(
        self,
        context: ExecutionContext,
        outcome: DriverOutcome,
        *,
        command: ExecutionCommand,
        operation_token: str,
    ) -> None:
        """Publish the public result, never private hand-off data."""
        if outcome.result is None:
            return
        self._publish_message(
            context,
            outcome,
            command=command,
            operation_token=operation_token,
        )
        self._publish_artifacts(context, outcome)

    def _publish_message(
        self,
        context: ExecutionContext,
        outcome: DriverOutcome,
        *,
        command: ExecutionCommand,
        operation_token: str,
    ) -> None:
        """Publish a bounded assistant message when the result has content."""
        publications = build_message_publications(
            context,
            outcome,
            command,
            operation_token,
            self._reservations.admitted_assistant_message_id(
                owner=context.owner_ref,
                execution_id=context.execution_id,
            ),
        )
        for publication in publications:
            self._append_publication(context, publication)

    def _publish_artifacts(
        self,
        context: ExecutionContext,
        outcome: DriverOutcome,
    ) -> None:
        """Publish public artifact facts and persist private delivery refs."""
        result = outcome.result
        if result is None:
            return
        for artifact in result.artifacts:
            if (
                self._target_store is not None
                and artifact.private_delivery_ref is not None
            ):
                try:
                    self._target_store.put(
                        build_artifact_target_binding(context, artifact)
                    )
                except ExecutionTargetBindingFenceError as exc:
                    raise ExecutionReservationConflictError(
                        "provider_join_lease_lost"
                    ) from exc
            self._append_publication(
                context,
                build_artifact_publication(outcome, artifact),
            )

    def _append_publication(
        self,
        context: ExecutionContext,
        publication: RuntimeEventPublication,
    ) -> None:
        """Append one fully prepared public Runtime event."""
        self._append(
            context,
            event_type=publication.event_type,
            status=publication.status,
            payload=publication.payload,
            summary_key=publication.summary[0],
            summary_text=publication.summary[1],
            operation_token=publication.operation_token,
            source=publication.source,
            target=publication.target,
        )

    def _append_input_action(
        self,
        context: ExecutionContext,
        *,
        command: ExecutionCommand,
        event_type: ExecutionEventType,
        outcome: str,
    ) -> None:
        """Record bounded action state without exposing submitted input."""
        assert command.action_id is not None
        assert command.expected_revision is not None
        surface_id = public_action_surface_id(command)
        self._append(
            context,
            event_type=event_type,
            status=(
                EventStatus.RUNNING
                if event_type is not ExecutionEventType.INPUT_ACTION_REJECTED
                else EventStatus.WAITING_INPUT
            ),
            payload={
                "surface_id": surface_id,
                "outcome": outcome,
                "action_revision": command.expected_revision,
            },
            summary_key=event_type.value,
            summary_text=event_type.value.replace(".", " ")
            .replace("_", " ")
            .title(),
            operation_token=f"resume:{command.action_id}:{outcome}",
        )

    async def _execute_driver(
        self,
        operation: DriverOperation,
        context: ExecutionContext,
        command: ExecutionCommand,
    ) -> DriverOutcome:
        services = ExecutionServices(
            journal=self._journal,
            reservations=self._reservations,
            work=self._work,
            clock=self._clock,
        )
        driver = self._drivers.get(context.agent.driver)
        if driver is None:
            return DriverOutcome.failed(code="driver_unavailable")
        outcome = DriverOutcome.failed(code="driver_unhandled_error")
        with suppress(Exception):
            try:
                with bind_execution_boundary(context, services):
                    outcome = await driver.execute(
                        operation, context, command, services
                    )
            except ExecutionRuntimeError as exc:
                outcome = DriverOutcome.failed(
                    code=exc.code,
                    retryable=exc.retryable,
                )
        return outcome

    def _record_nonterminal(
        self,
        context: ExecutionContext,
        root_span_revision: int,
        outcome: DriverOutcome,
        *,
        operation_token: str,
    ) -> None:
        current = self._reservations.get(
            owner=context.owner_ref,
            execution_id=context.execution_id,
        )
        span_status = SpanStatus.RUNNING
        next_attempt_at: str | None = None
        if outcome.status is ExecutionStatus.WAITING_INPUT:
            span_status = SpanStatus.WAITING_INPUT
            self._append(
                context,
                event_type=ExecutionEventType.EXECUTION_WAITING_INPUT,
                status=EventStatus.WAITING_INPUT,
                payload={},
                summary_key="execution.waiting_input",
                summary_text="Execution is waiting for input",
                operation_token=operation_token,
            )
            self._append(
                context,
                event_type=ExecutionEventType.SPAN_WAITING_INPUT,
                status=EventStatus.WAITING_INPUT,
                payload={"phase": context.agent.slug},
                summary_key=f"agent.{context.agent.slug}.waiting_input",
                summary_text="Agent is waiting for input",
                operation_token=operation_token,
            )
        elif outcome.retry_after_ms is not None:
            span_status = SpanStatus.RETRY_SCHEDULED
            next_attempt_at = (
                self._clock() + timedelta(milliseconds=outcome.retry_after_ms)
            ).isoformat()
            self._append(
                context,
                event_type=ExecutionEventType.SPAN_RETRY_SCHEDULED,
                status=EventStatus.RETRY_SCHEDULED,
                payload={
                    "delay_ms": outcome.retry_after_ms,
                    "next_attempt": 2,
                    "code": "driver_retry",
                },
                summary_key=f"agent.{context.agent.slug}.retry_scheduled",
                summary_text="Agent retry scheduled",
                operation_token=operation_token,
            )

        if outcome.tracking_health is TrackingHealth.DEGRADED:
            self._append_tracking(
                context,
                degraded=True,
                operation_token=operation_token,
            )
        elif current.tracking_health == TrackingHealth.DEGRADED.value:
            self._append_tracking(
                context,
                degraded=False,
                operation_token=operation_token,
            )

        cancellation_state = (
            outcome.cancellation_outcome or current.cancellation_state
        )
        if outcome.cancellation_outcome is not None:
            self._append(
                context,
                event_type=ExecutionEventType.EXECUTION_CANCELLATION_REQUESTED,
                status=EventStatus.CANCELLATION_REQUESTED,
                payload={"outcome": outcome.cancellation_outcome},
                summary_key="execution.cancellation_observed",
                summary_text="Cancellation state updated",
                operation_token=f"{operation_token}:outcome",
            )
        # A non-terminal RUNNING observation (notably best-effort cancellation)
        # does not transition the root span. Advancing its optimistic revision
        # here would fence out the still-running START operation when its real
        # provider result arrives later.
        if span_status is not SpanStatus.RUNNING:
            self._work.update_span_status(
                context.execution_id,
                context.root_span_id,
                owner=context.owner_ref,
                status=span_status,
                expected_revision=root_span_revision,
            )
        self._reservations.record_observation(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            status=outcome.status,
            tracking_health=outcome.tracking_health.value,
            cancellation_state=cancellation_state,
            next_attempt_at=next_attempt_at,
        )

    def _append_tracking(
        self,
        context: ExecutionContext,
        *,
        degraded: bool,
        operation_token: str,
    ) -> None:
        self._append(
            context,
            event_type=(
                ExecutionEventType.TRACKING_DEGRADED
                if degraded
                else ExecutionEventType.TRACKING_RECOVERED
            ),
            status=EventStatus.DEGRADED if degraded else EventStatus.RUNNING,
            payload={
                "health": "degraded" if degraded else "healthy",
                "code": "driver_tracking_unavailable" if degraded else None,
                "retryable": degraded,
            },
            summary_key=(
                "tracking.degraded" if degraded else "tracking.recovered"
            ),
            summary_text=(
                "Execution tracking is degraded"
                if degraded
                else "Execution tracking recovered"
            ),
            operation_token=operation_token,
        )

    def _complete_operation(
        self,
        context: ExecutionContext,
        operation_id: str,
        outcome: DriverOutcome,
    ) -> None:
        self._reservations.complete_operation(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            operation_id=operation_id,
            outcome_json=serialize_driver_outcome(outcome),
        )

    def _settle_terminal(
        self,
        context: ExecutionContext,
        root_span_revision: int,
        outcome: DriverOutcome,
    ) -> None:
        # Root-span revision is retained in this private call shape for driver
        # compatibility.  The canonical repository now reads and updates the
        # compatible span inside the same transaction as the reservation CAS.
        del root_span_revision
        self._append_todo_snapshot(
            context,
            state=(
                "completed"
                if outcome.status is ExecutionStatus.SUCCEEDED
                else "failed"
            ),
            operation_token=f"terminal:{outcome.status.value}",
        )
        try:
            authority = self._reservations.terminal_authority(context)
            settled = self._reservations.settle_terminal(authority, outcome)
        except (sqlite3.Error, OSError) as exc:
            raise ExecutionRuntimeError("terminal_settlement_failed") from exc
        if settled:
            self._publish_execution_log(context, outcome)
            return
        current = self._reservations.get(
            owner=context.owner_ref,
            execution_id=context.execution_id,
        )
        if current.status == outcome.status.value:
            return
        raise ExecutionRuntimeError("terminal_settlement_conflict")

    def _publish_execution_log(
        self,
        context: ExecutionContext,
        outcome: DriverOutcome,
    ) -> None:
        """Best-effort publication of already-public operation facts."""
        if self._execution_log_store is None or self._target_store is None:
            return
        with suppress(Exception):
            self._publish_execution_log_unchecked(context, outcome)

    def _publish_execution_log_unchecked(
        self,
        context: ExecutionContext,
        outcome: DriverOutcome,
    ) -> None:
        """Publish an execution log after the optional stores are present."""
        assert self._execution_log_store is not None
        assert self._target_store is not None
        events: list[ExecutionEventV2] = []
        after_seq = 0
        while True:
            page = self._journal.list_events(
                context.execution_id,
                owner=context.owner_ref,
                after_seq=after_seq,
                limit=200,
            )
            if page is None:
                return
            events.extend(page.items)
            after_seq = page.next_after_seq
            if not page.has_more:
                break
        if not any(event.work_unit_id is not None for event in events):
            return
        payload = build_execution_log_document(context.execution_id, events)
        artifact = self._execution_log_store.put(
            owner=context.owner_ref,
            execution_id=context.execution_id,
            payload=payload,
        )
        self._target_store.put(
            build_execution_log_target_binding(
                owner=context.owner_ref,
                execution_id=context.execution_id,
                artifact=artifact,
            )
        )
        self._append(
            context,
            event_type=ExecutionEventType.ARTIFACT_PUBLISHED,
            status=EventStatus(outcome.status.value),
            payload=artifact_public_payload(artifact),
            summary_key="result.execution_log.published",
            summary_text="Execution log published",
            operation_token=f"execution-log:{artifact.target_id}",
            source="artifact",
            target={"kind": "artifact", "id": artifact.target_id},
        )

    def _append_todo_snapshot(
        self,
        context: ExecutionContext,
        *,
        state: str,
        operation_token: str = "start",
    ) -> None:
        """Close an Agent-declared plan without inventing Todo state."""
        projection = self._journal.get_projection(
            context.execution_id,
            owner=context.owner_ref,
        )
        if not projection.todo_declared or not projection.todos:
            return
        items = projection.todos
        if state == "completed":
            statuses = ("completed",) * len(items)
        else:
            failed_index = next(
                (
                    index
                    for index, item in enumerate(items)
                    if item.status != "completed"
                ),
                len(items) - 1,
            )
            statuses = tuple(
                (
                    "completed"
                    if index < failed_index
                    else "failed" if index == failed_index else "skipped"
                )
                for index in range(len(items))
            )
        self._append(
            context,
            event_type=ExecutionEventType.TODO_SNAPSHOT,
            status=(
                EventStatus.RUNNING
                if state == "running"
                else (
                    EventStatus.SUCCEEDED
                    if state == "completed"
                    else EventStatus.FAILED
                )
            ),
            payload={
                "items": [
                    {
                        "id": item.id,
                        "label_key": item.label_key,
                        "status": status,
                    }
                    for item, status in zip(items, statuses, strict=True)
                ]
            },
            summary_key="todo.snapshot",
            summary_text="Todo plan updated",
            operation_token=operation_token,
            target={"kind": "todo", "id": f"todo:{context.agent.slug}"},
        )

    def _append(
        self,
        context: ExecutionContext,
        **fields: Unpack[RuntimeAppendFields],
    ) -> None:
        event_type = fields["event_type"]
        operation_token = fields.get("operation_token", "start")
        intent = parse_execution_event_intent_v2(
            {
                "type": event_type.value,
                "status": fields["status"].value,
                "source": fields.get("source", "runtime"),
                "span_id": context.current_span_id,
                "parent_span_id": context.parent_span_id,
                "attempt": 1,
                "summary": {
                    "key": fields["summary_key"],
                    "text": fields["summary_text"],
                },
                "public_payload": fields["payload"],
                "target": fields.get("target"),
                "idempotency_key": (
                    f"runtime:{context.current_span_id}:{event_type.value}:"
                    f"{operation_token}"
                ),
            }
        )
        try:
            self._journal.append(
                context.execution_id,
                owner=context.owner_ref,
                intent=intent,
            )
        except ExecutionJournalPublicationFenceError as exc:
            raise ExecutionReservationConflictError(
                "provider_join_lease_lost"
            ) from exc
