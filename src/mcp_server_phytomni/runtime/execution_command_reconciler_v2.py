# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded recovery owner for ambiguous detached execution commands."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ..storage.path_policy import IdFactory
from .execution_command_dispatcher_v2 import (
    ClaimedReconcileCommand,
    SQLiteExecutionCommandQueueV2,
)
from .execution_command_support_v2 import (
    command_identity,
    conversation_identity,
    projection_has_terminal,
    supervisor_settlement_authority,
)
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
    execution_command_hash,
    routed_binding_matches_command,
)
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
)
from .sqlite import sqlite_transaction

_LOGGER = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    ExecutionStatus.SUCCEEDED.value,
    ExecutionStatus.PARTIAL.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.CANCELLED.value,
    ExecutionStatus.TIMED_OUT.value,
}
_EVIDENCE_COUNT_QUERIES = {
    "execution_events_v2": (
        "SELECT COUNT(*) FROM execution_events_v2 WHERE owner_ref = ? "
        "AND execution_id = ?"
    ),
    "execution_spans": (
        "SELECT COUNT(*) FROM execution_spans WHERE owner_ref = ? "
        "AND execution_id = ?"
    ),
    "execution_work_units": (
        "SELECT COUNT(*) FROM execution_work_units WHERE owner_ref = ? "
        "AND execution_id = ?"
    ),
    "execution_target_bindings_v2": (
        "SELECT COUNT(*) FROM execution_target_bindings_v2 "
        "WHERE owner_ref = ? AND execution_id = ?"
    ),
    "execution_operations_v2": (
        "SELECT COUNT(*) FROM execution_operations_v2 WHERE owner_ref = ? "
        "AND execution_id = ?"
    ),
}


class ReconcileDisposition(StrEnum):
    """Finite outcomes for one reconcile poll."""

    IDLE = "idle"
    ACKNOWLEDGED = "acknowledged"
    REDISPATCHED = "redispatched"
    DEFERRED = "deferred"
    TERMINALIZED = "terminalized"


@dataclass(frozen=True, slots=True)
class _ReservationEvidence:
    """Reservation identity and deadline captured in one snapshot."""

    status: str
    deadline_at: str
    command_hash: str
    agent: str
    fingerprint_version: object
    fingerprint: object


@dataclass(frozen=True, slots=True)
class _ProjectionEvidence:
    """Journal projection evidence relevant to safe replay."""

    terminal: bool
    latest_seq: int


@dataclass(frozen=True, slots=True)
class _RuntimeEvidence:
    """Durable Runtime side-effect counts for one execution."""

    event_count: int
    span_count: int
    work_unit_count: int
    provider_identity_count: int
    result_count: int
    operation_count: int


@dataclass(frozen=True, slots=True)
class _ConversationEvidence:
    """Conversation turn identity and replayable failure state."""

    conversation_key: str | None
    turn_id: str | None
    turn_state: str | None


@dataclass(frozen=True, slots=True)
class _BindingEvidence:
    """Canonical and router-authorized command binding results."""

    canonical_matches: bool
    routed_matches: bool


@dataclass(frozen=True, slots=True)
class _ReconcileReservationEvidence:
    """Reservation and projection facts for one reconcile snapshot."""

    reservation_status: str
    deadline_at: str
    projection_terminal: bool
    projection_latest_seq: int


@dataclass(frozen=True, slots=True)
class _ReconcileRuntimeEvidence(_ReconcileReservationEvidence):
    """Durable Runtime side-effect counts for one reconcile snapshot."""

    event_count: int
    span_count: int
    work_unit_count: int
    provider_identity_count: int
    result_count: int
    operation_count: int


@dataclass(frozen=True, slots=True)
class ReconcileEvidence(_ReconcileRuntimeEvidence):
    """One transactionally consistent, bounded recovery observation."""

    conversation_key: str | None
    turn_id: str | None
    conversation_turn_state: str | None
    canonical_identity_matches: bool
    routed_binding_matches: bool

    @property
    def terminal(self) -> bool:
        """Return whether reservation or journal evidence is terminal."""
        return (
            self.reservation_status in _TERMINAL_STATUSES
            or self.projection_terminal
        )

    @property
    def runtime_started(self) -> bool:
        """Return whether any durable Runtime side effect is observable."""
        return (
            self.reservation_status != ExecutionStatus.ADMITTED.value
            or self.projection_terminal
            or self.projection_latest_seq > 0
            or any(
                count > 0
                for count in (
                    self.event_count,
                    self.span_count,
                    self.work_unit_count,
                    self.provider_identity_count,
                    self.result_count,
                    self.operation_count,
                )
            )
        )

    @property
    def provably_unstarted(self) -> bool:
        """Return whether all authorities prove dispatch never started."""
        runtime_absent = all(
            count == 0
            for count in (
                self.event_count,
                self.span_count,
                self.work_unit_count,
                self.provider_identity_count,
                self.result_count,
                self.operation_count,
            )
        )
        return (
            self.reservation_status == ExecutionStatus.ADMITTED.value
            and not self.projection_terminal
            and self.projection_latest_seq == 0
            and runtime_absent
            and self.conversation_key is not None
            and self.turn_id is not None
            and self.conversation_turn_state == "failed"
            and (
                self.canonical_identity_matches or self.routed_binding_matches
            )
        )

    def deadline_expired(self, now: datetime) -> bool:
        """Return whether the bounded execution deadline has elapsed."""
        try:
            deadline = datetime.fromisoformat(self.deadline_at)
        except (TypeError, ValueError):
            return True
        if deadline.utcoffset() is None:
            return True
        return now >= deadline


def read_reconcile_evidence(
    *, db_path: str, claim: ClaimedReconcileCommand
) -> ReconcileEvidence:
    """Read all start/terminal authorities in one SQLite snapshot."""
    with sqlite_transaction(db_path, timeout=10) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN")
        reservation = _read_reservation_evidence(connection, claim)
        projection = _read_projection_evidence(connection, claim)
        runtime = _read_runtime_evidence(connection, claim)
        conversation = _read_conversation_evidence(connection, claim)
        binding = _read_binding_evidence(claim, reservation)
        connection.commit()
    return ReconcileEvidence(
        reservation_status=reservation.status,
        deadline_at=reservation.deadline_at,
        projection_terminal=projection.terminal,
        projection_latest_seq=projection.latest_seq,
        event_count=runtime.event_count,
        span_count=runtime.span_count,
        work_unit_count=runtime.work_unit_count,
        provider_identity_count=runtime.provider_identity_count,
        result_count=runtime.result_count,
        operation_count=runtime.operation_count,
        conversation_key=conversation.conversation_key,
        turn_id=conversation.turn_id,
        conversation_turn_state=conversation.turn_state,
        canonical_identity_matches=binding.canonical_matches,
        routed_binding_matches=binding.routed_matches,
    )


def _read_reservation_evidence(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
) -> _ReservationEvidence:
    reservation = connection.execute(
        "SELECT status, execution_deadline_at, execution_command_hash, "
        "agent, execution_fingerprint_version, execution_fingerprint "
        "FROM runs WHERE user_id = ? AND execution_id = ? "
        "AND execution_tombstoned_at IS NULL",
        (claim.owner_ref, claim.execution_id),
    ).fetchone()
    if reservation is None:
        raise RuntimeError("reconcile_reservation_missing")
    return _ReservationEvidence(
        status=str(reservation[0]),
        deadline_at=str(reservation[1]),
        command_hash=str(reservation[2]),
        agent=str(reservation[3]),
        fingerprint_version=reservation[4],
        fingerprint=reservation[5],
    )


def _read_projection_evidence(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
) -> _ProjectionEvidence:
    projection = connection.execute(
        "SELECT latest_seq, projection_json FROM execution_projection_v2 "
        "WHERE owner_ref = ? AND execution_id = ?",
        (claim.owner_ref, claim.execution_id),
    ).fetchone()
    return _ProjectionEvidence(
        latest_seq=int(projection[0]) if projection else 0,
        terminal=projection_has_terminal(
            projection[1] if projection else None
        ),
    )


def _read_runtime_evidence(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
) -> _RuntimeEvidence:
    provider_identity_count = connection.execute(
        "SELECT COUNT(*) FROM execution_work_units "
        "WHERE owner_ref = ? AND execution_id = ? "
        "AND provider_task_id IS NOT NULL",
        (claim.owner_ref, claim.execution_id),
    ).fetchone()[0]
    return _RuntimeEvidence(
        event_count=_count(connection, "execution_events_v2", claim),
        span_count=_count(connection, "execution_spans", claim),
        work_unit_count=_count(connection, "execution_work_units", claim),
        provider_identity_count=int(provider_identity_count),
        result_count=_count(
            connection,
            "execution_target_bindings_v2",
            claim,
        ),
        operation_count=_count(
            connection,
            "execution_operations_v2",
            claim,
        ),
    )


def _read_conversation_evidence(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
) -> _ConversationEvidence:
    conversation_key, turn_id = conversation_identity(claim.command)
    turn = (
        connection.execute(
            "SELECT state FROM conversation_turns "
            "WHERE conversation_key = ? AND turn_id = ?",
            (conversation_key, turn_id),
        ).fetchone()
        if conversation_key is not None and turn_id is not None
        else None
    )
    return _ConversationEvidence(
        conversation_key=conversation_key,
        turn_id=turn_id,
        turn_state=str(turn[0]) if turn else None,
    )


def _read_binding_evidence(
    claim: ClaimedReconcileCommand,
    reservation: _ReservationEvidence,
) -> _BindingEvidence:
    return _BindingEvidence(
        canonical_matches=_canonical_identity_matches(claim, reservation),
        routed_matches=routed_binding_matches_command(
            claim.command,
            owner=claim.owner_ref,
            execution_id=claim.execution_id,
            reservation_agent=reservation.agent,
            reservation_command_hash=reservation.command_hash,
            fingerprint_version=reservation.fingerprint_version,
            fingerprint=reservation.fingerprint,
        ),
    )


async def reconcile_one_execution_command(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    worker_id: str,
    db_path: str,
    clock: Callable[[], datetime] | None = None,
) -> ReconcileDisposition:
    """Own one due reconcile row and apply the bounded decision table."""
    claim = await asyncio.to_thread(queue.claim_reconcile, worker_id=worker_id)
    if claim is None:
        return ReconcileDisposition.IDLE
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.utcoffset() is None:
        raise ValueError("reconcile clock must be timezone-aware")
    evidence = await _read_reconcile_evidence_capturing_failure(
        db_path=db_path,
        claim=claim,
    )
    if isinstance(evidence, Exception):
        _LOGGER.warning(
            "Execution command reconciliation evidence read failed "
            "error_type=%s",
            type(evidence).__name__,
        )
        await asyncio.to_thread(
            queue.reschedule_reconcile,
            claim,
            code="reconcile_evidence_unavailable",
            delay_seconds=_backoff(claim.reconcile_attempt),
        )
        return ReconcileDisposition.DEFERRED
    return await _apply_reconcile_evidence(
        queue=queue,
        claim=claim,
        db_path=db_path,
        now=now,
        evidence=evidence,
    )


async def _read_reconcile_evidence_capturing_failure(
    *,
    db_path: str,
    claim: ClaimedReconcileCommand,
) -> ReconcileEvidence | Exception:
    outcome = (
        await asyncio.gather(
            asyncio.to_thread(
                read_reconcile_evidence,
                db_path=db_path,
                claim=claim,
            ),
            return_exceptions=True,
        )
    )[0]
    if isinstance(outcome, BaseException):
        if isinstance(outcome, Exception):
            return outcome
        raise outcome
    return outcome


async def _apply_reconcile_evidence(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedReconcileCommand,
    db_path: str,
    now: datetime,
    evidence: ReconcileEvidence,
) -> ReconcileDisposition:
    if evidence.terminal or evidence.runtime_started:
        return await _resolve_reconcile(queue, claim)
    if evidence.deadline_expired(now):
        return await _terminalize_deadline(
            queue=queue,
            claim=claim,
            db_path=db_path,
            now=now,
        )
    if evidence.provably_unstarted and claim.redispatch_count == 0:
        return await _reconcile_provably_unstarted(
            queue=queue,
            claim=claim,
            db_path=db_path,
            now=now,
        )
    return await _defer_ambiguous_reconcile(queue, claim, db_path)


async def _reconcile_provably_unstarted(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedReconcileCommand,
    db_path: str,
    now: datetime,
) -> ReconcileDisposition:
    fenced = await asyncio.to_thread(
        read_reconcile_evidence,
        db_path=db_path,
        claim=claim,
    )
    if fenced.terminal or fenced.runtime_started:
        return await _resolve_reconcile(queue, claim)
    if fenced.deadline_expired(now):
        return await _terminalize_deadline(
            queue=queue,
            claim=claim,
            db_path=db_path,
            now=now,
        )
    if fenced.provably_unstarted:
        assert fenced.conversation_key is not None
        assert fenced.turn_id is not None
        redispatched = await asyncio.to_thread(
            queue.release_failed_turn_and_redispatch,
            claim,
            conversation_key=fenced.conversation_key,
            turn_id=fenced.turn_id,
            allow_routed_binding=fenced.routed_binding_matches,
        )
        if redispatched:
            return ReconcileDisposition.REDISPATCHED
    return await _defer_ambiguous_reconcile(queue, claim, db_path)


async def _resolve_reconcile(
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedReconcileCommand,
) -> ReconcileDisposition:
    await asyncio.to_thread(queue.resolve_reconcile, claim)
    return ReconcileDisposition.ACKNOWLEDGED


async def _defer_ambiguous_reconcile(
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedReconcileCommand,
    db_path: str,
) -> ReconcileDisposition:
    await asyncio.to_thread(
        _mark_reconcile_degraded,
        db_path,
        claim,
        "reconcile_ambiguous",
    )
    await asyncio.to_thread(
        queue.reschedule_reconcile,
        claim,
        code="reconcile_ambiguous",
        delay_seconds=_backoff(claim.reconcile_attempt),
    )
    return ReconcileDisposition.DEFERRED


async def run_execution_command_reconciler(
    *,
    db_path: str,
    stop: asyncio.Event,
    poll_seconds: float = 0.5,
) -> None:
    """Run the process-lifetime dedicated reconcile claimant."""
    SQLiteExecutionReservationRepository(db_path)
    queue = SQLiteExecutionCommandQueueV2(db_path)
    worker_id = IdFactory().new_id("worker", "reconcile")
    while not stop.is_set():
        disposition = await reconcile_one_execution_command(
            queue=queue,
            worker_id=worker_id,
            db_path=db_path,
        )
        if disposition is ReconcileDisposition.IDLE:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


async def _terminalize_deadline(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedReconcileCommand,
    db_path: str,
    now: datetime,
) -> ReconcileDisposition:
    repository = SQLiteExecutionReservationRepository(db_path)
    record = await asyncio.to_thread(
        repository.get,
        owner=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    if record.status.value in _TERMINAL_STATUSES:
        await asyncio.to_thread(queue.resolve_reconcile, claim)
        return ReconcileDisposition.ACKNOWLEDGED
    settled = await asyncio.to_thread(
        repository.settle_terminal,
        supervisor_settlement_authority(
            owner_ref=claim.owner_ref,
            execution_id=claim.execution_id,
            expected_revision=record.supervisor_revision,
            issued_at=now,
        ),
        DriverOutcome.failed(code="dispatch_unresolved"),
    )
    if settled:
        await asyncio.to_thread(queue.resolve_reconcile, claim)
        return ReconcileDisposition.TERMINALIZED
    current = await asyncio.to_thread(
        repository.get,
        owner=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    if current.status.value in _TERMINAL_STATUSES:
        await asyncio.to_thread(queue.resolve_reconcile, claim)
        return ReconcileDisposition.ACKNOWLEDGED
    await asyncio.to_thread(
        queue.reschedule_reconcile,
        claim,
        code="reconcile_terminal_race",
        delay_seconds=_backoff(claim.reconcile_attempt),
    )
    return ReconcileDisposition.DEFERRED


def _canonical_identity_matches(
    claim: ClaimedReconcileCommand,
    reservation: _ReservationEvidence,
) -> bool:
    command = claim.command
    identity = command_identity(
        command,
        owner_ref=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    if (
        identity is None
        or identity.agent != reservation.agent
        or command.get("fingerprint_version")
        != reservation.fingerprint_version
        or command.get("fingerprint") != reservation.fingerprint
    ):
        return False
    try:
        candidate_hash = execution_command_hash(
            ExecutionCommand(
                agent_slug=identity.agent,
                arguments=identity.arguments,
            )
        )
    except (TypeError, ValueError):
        return False
    return candidate_hash == reservation.command_hash


def _count(
    connection: sqlite3.Connection,
    table: str,
    claim: ClaimedReconcileCommand,
) -> int:
    return int(
        connection.execute(
            _EVIDENCE_COUNT_QUERIES[table],
            (claim.owner_ref, claim.execution_id),
        ).fetchone()[0]
    )


def _mark_reconcile_degraded(
    db_path: str,
    claim: ClaimedReconcileCommand,
    code: str,
) -> None:
    with sqlite_transaction(db_path, timeout=10) as connection:
        connection.execute(
            "UPDATE runs SET execution_tracking_health = 'degraded', "
            "updated_at = ? WHERE user_id = ? AND execution_id = ? "
            "AND status = 'admitted' AND execution_terminal_outcome IS NULL",
            (
                datetime.now(UTC).isoformat(),
                claim.owner_ref,
                claim.execution_id,
            ),
        )
        connection.execute(
            "UPDATE execution_commands_v2 SET last_error_code = ? "
            "WHERE owner_ref = ? AND execution_id = ? "
            "AND state = 'reconcile'",
            (code, claim.owner_ref, claim.execution_id),
        )
        connection.commit()


def _backoff(attempt: int) -> int:
    return min(60, 2 ** min(max(attempt, 1), 5))


__all__ = [
    "ReconcileDisposition",
    "ReconcileEvidence",
    "read_reconcile_evidence",
    "reconcile_one_execution_command",
    "run_execution_command_reconciler",
]
