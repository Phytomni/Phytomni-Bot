# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded recovery owner for ambiguous detached execution commands."""

from __future__ import annotations

import asyncio
import json
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
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
    execution_command_hash,
    routed_binding_matches_command,
)
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)

_LOGGER = logging.getLogger(__name__)
_TERMINAL_STATUSES = {
    ExecutionStatus.SUCCEEDED.value,
    ExecutionStatus.PARTIAL.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.CANCELLED.value,
    ExecutionStatus.TIMED_OUT.value,
}


class ReconcileDisposition(StrEnum):
    """Finite outcomes for one reconcile poll."""

    IDLE = "idle"
    ACKNOWLEDGED = "acknowledged"
    REDISPATCHED = "redispatched"
    DEFERRED = "deferred"
    TERMINALIZED = "terminalized"


@dataclass(frozen=True, slots=True)
class ReconcileEvidence:
    """One transactionally consistent, bounded recovery observation."""

    reservation_status: str
    deadline_at: str
    projection_terminal: bool
    projection_latest_seq: int
    event_count: int
    span_count: int
    work_unit_count: int
    provider_identity_count: int
    result_count: int
    operation_count: int
    conversation_key: str | None
    turn_id: str | None
    conversation_turn_state: str | None
    canonical_identity_matches: bool
    routed_binding_matches: bool

    @property
    def terminal(self) -> bool:
        return (
            self.reservation_status in _TERMINAL_STATUSES
            or self.projection_terminal
        )

    @property
    def runtime_started(self) -> bool:
        return (
            self.reservation_status != ExecutionStatus.ADMITTED.value
            or self.projection_latest_seq > 0
            or self.event_count > 0
            or self.span_count > 0
            or self.work_unit_count > 0
            or self.provider_identity_count > 0
            or self.result_count > 0
            or self.operation_count > 0
        )

    @property
    def provably_unstarted(self) -> bool:
        return (
            self.reservation_status == ExecutionStatus.ADMITTED.value
            and not self.projection_terminal
            and self.projection_latest_seq == 0
            and self.event_count == 0
            and self.span_count == 0
            and self.work_unit_count == 0
            and self.provider_identity_count == 0
            and self.result_count == 0
            and self.operation_count == 0
            and self.conversation_key is not None
            and self.turn_id is not None
            and self.conversation_turn_state == "failed"
            and (
                self.canonical_identity_matches or self.routed_binding_matches
            )
        )

    def deadline_expired(self, now: datetime) -> bool:
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
    with sqlite3.connect(db_path, timeout=10) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN")
        reservation = connection.execute(
            "SELECT status, execution_deadline_at, execution_command_hash, "
            "agent, execution_fingerprint_version, execution_fingerprint "
            "FROM runs WHERE user_id = ? AND execution_id = ? "
            "AND execution_tombstoned_at IS NULL",
            (claim.owner_ref, claim.execution_id),
        ).fetchone()
        if reservation is None:
            raise RuntimeError("reconcile_reservation_missing")
        projection = connection.execute(
            "SELECT latest_seq, projection_json FROM execution_projection_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            (claim.owner_ref, claim.execution_id),
        ).fetchone()
        projection_latest_seq = int(projection[0]) if projection else 0
        projection_terminal = _projection_has_terminal(
            projection[1] if projection else None
        )
        event_count = _count(connection, "execution_events_v2", claim)
        span_count = _count(connection, "execution_spans", claim)
        work_unit_count = _count(connection, "execution_work_units", claim)
        provider_identity_count = connection.execute(
            "SELECT COUNT(*) FROM execution_work_units "
            "WHERE owner_ref = ? AND execution_id = ? "
            "AND provider_task_id IS NOT NULL",
            (claim.owner_ref, claim.execution_id),
        ).fetchone()[0]
        result_count = _count(
            connection, "execution_target_bindings_v2", claim
        )
        operation_count = _count(connection, "execution_operations_v2", claim)
        conversation_key, turn_id = _conversation_identity(claim.command)
        turn = (
            connection.execute(
                "SELECT state FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (conversation_key, turn_id),
            ).fetchone()
            if conversation_key is not None and turn_id is not None
            else None
        )
        canonical_matches = _canonical_identity_matches(
            claim,
            reservation_hash=str(reservation[2]),
            reservation_agent=str(reservation[3]),
            fingerprint_version=reservation[4],
            fingerprint=reservation[5],
        )
        routed_matches = routed_binding_matches_command(
            claim.command,
            owner=claim.owner_ref,
            execution_id=claim.execution_id,
            reservation_agent=str(reservation[3]),
            reservation_command_hash=str(reservation[2]),
            fingerprint_version=reservation[4],
            fingerprint=reservation[5],
        )
        connection.commit()
    return ReconcileEvidence(
        reservation_status=str(reservation[0]),
        deadline_at=str(reservation[1]),
        projection_terminal=projection_terminal,
        projection_latest_seq=projection_latest_seq,
        event_count=int(event_count),
        span_count=int(span_count),
        work_unit_count=int(work_unit_count),
        provider_identity_count=int(provider_identity_count),
        result_count=int(result_count),
        operation_count=int(operation_count),
        conversation_key=conversation_key,
        turn_id=turn_id,
        conversation_turn_state=str(turn[0]) if turn else None,
        canonical_identity_matches=canonical_matches,
        routed_binding_matches=routed_matches,
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
    try:
        evidence = await asyncio.to_thread(
            read_reconcile_evidence,
            db_path=db_path,
            claim=claim,
        )
    except Exception as exc:
        _LOGGER.warning(
            "Execution command reconciliation evidence read failed "
            "error_type=%s",
            type(exc).__name__,
        )
        await asyncio.to_thread(
            queue.reschedule_reconcile,
            claim,
            code="reconcile_evidence_unavailable",
            delay_seconds=_backoff(claim.reconcile_attempt),
        )
        return ReconcileDisposition.DEFERRED

    if evidence.terminal or evidence.runtime_started:
        await asyncio.to_thread(queue.resolve_reconcile, claim)
        return ReconcileDisposition.ACKNOWLEDGED
    if evidence.deadline_expired(now):
        return await _terminalize_deadline(
            queue=queue,
            claim=claim,
            db_path=db_path,
            now=now,
        )
    if evidence.provably_unstarted and claim.redispatch_count == 0:
        fenced = await asyncio.to_thread(
            read_reconcile_evidence,
            db_path=db_path,
            claim=claim,
        )
        if fenced.terminal or fenced.runtime_started:
            await asyncio.to_thread(queue.resolve_reconcile, claim)
            return ReconcileDisposition.ACKNOWLEDGED
        if fenced.deadline_expired(now):
            return await _terminalize_deadline(
                queue=queue,
                claim=claim,
                db_path=db_path,
                now=now,
            )
        if not fenced.provably_unstarted:
            evidence = fenced
        else:
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
        TerminalSettlementAuthority(
            owner_ref=claim.owner_ref,
            execution_id=claim.execution_id,
            expected_revision=record.supervisor_revision,
            actor="supervisor",
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
    *,
    reservation_hash: str,
    reservation_agent: str,
    fingerprint_version: object,
    fingerprint: object,
) -> bool:
    command = claim.command
    agent = command.get("agent")
    arguments = command.get("arguments")
    version = command.get("fingerprint_version")
    candidate_fingerprint = command.get("fingerprint")
    if (
        command.get("owner_ref") != claim.owner_ref
        or command.get("execution_id") != claim.execution_id
        or not isinstance(agent, str)
        or not isinstance(arguments, dict)
        or agent != reservation_agent
        or version != fingerprint_version
        or candidate_fingerprint != fingerprint
    ):
        return False
    try:
        candidate_hash = execution_command_hash(
            ExecutionCommand(agent_slug=agent, arguments=arguments)
        )
    except (TypeError, ValueError):
        return False
    return candidate_hash == reservation_hash


def _conversation_identity(
    command: dict[str, object],
) -> tuple[str | None, str | None]:
    arguments = command.get("arguments")
    conversation = (
        arguments.get("__conversation")
        if isinstance(arguments, dict)
        else None
    )
    if not isinstance(conversation, dict):
        return None, None
    key = conversation.get("conversation_key")
    turn = conversation.get("turn_id")
    return (
        key if isinstance(key, str) and key else None,
        str(turn) if isinstance(turn, (str, int)) else None,
    )


def _projection_has_terminal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        projection = json.loads(value)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(projection, dict) and projection.get("terminal") is not None
    )


def _count(
    connection: sqlite3.Connection,
    table: str,
    claim: ClaimedReconcileCommand,
) -> int:
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE owner_ref = ? "  # noqa: S608
            "AND execution_id = ?",
            (claim.owner_ref, claim.execution_id),
        ).fetchone()[0]
    )


def _mark_reconcile_degraded(
    db_path: str,
    claim: ClaimedReconcileCommand,
    code: str,
) -> None:
    with sqlite3.connect(db_path, timeout=10) as connection:
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
