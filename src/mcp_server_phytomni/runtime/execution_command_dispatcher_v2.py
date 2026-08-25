# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable detached consumer for service-admitted execution commands."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from ..public_agent_catalog import public_agent_spec
from ..storage.path_policy import IdFactory
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    SQLiteExecutionReservationRepository,
    execution_command_hash,
    routed_binding_matches_command,
)
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)
from .request_context import request_context

InvokeCommand = Callable[..., Awaitable[Any]]
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedExecutionCommand:
    owner_ref: str
    execution_id: str
    command: dict[str, Any]
    attempt: int
    revision: int


@dataclass(frozen=True, slots=True)
class ClaimedReconcileCommand:
    """A reconcile row leased without entering normal processing state."""

    owner_ref: str
    execution_id: str
    command: dict[str, Any]
    attempt: int
    reconcile_attempt: int
    redispatch_count: int
    revision: int
    lease_owner: str


class DispatchClassification(StrEnum):
    """Finite delivery outcome shared with the Web outbox contract."""

    RETRY = "retry"
    RECONCILE = "reconcile"
    REJECT = "reject"


class InvocationBoundary(StrEnum):
    """Whether a delivery attempt may already have caused side effects."""

    NOT_ENTERED = "not_entered"
    ENTERED = "entered"
    DURABLY_CLAIMED = "durably_claimed"


@dataclass(frozen=True, slots=True)
class ClassifiedDispatchFailure:
    classification: DispatchClassification
    code: str
    boundary_state: InvocationBoundary


class SQLiteExecutionCommandQueueV2:
    """Lease-based command queue sharing the execution registry database."""

    def __init__(self, db_path: str, *, clock=None) -> None:
        self.db_path = db_path
        self._clock = clock or (lambda: datetime.now(UTC))

    def claim(
        self, *, worker_id: str, lease_seconds: int = 30
    ) -> ClaimedExecutionCommand | None:
        now = self._clock()
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_ref, execution_id, command_json, attempt, revision "
                "FROM execution_commands_v2 WHERE "
                "((state IN ('pending','retry') AND "
                "(next_attempt_at IS NULL OR next_attempt_at <= ?)) OR "
                "(state = 'processing' AND lease_expires_at < ?)) "
                "ORDER BY created_at, execution_id LIMIT 1",
                (now_text, now_text),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            result = connection.execute(
                "UPDATE execution_commands_v2 SET state = 'processing', "
                "attempt = attempt + 1, lease_owner = ?, lease_expires_at = ?, "
                "updated_at = ?, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? AND revision = ?",
                (worker_id, lease_text, now_text, row[0], row[1], row[4]),
            )
            connection.commit()
            if result.rowcount != 1:
                return None
        try:
            command = json.loads(str(row[2]))
        except (TypeError, ValueError):
            command = {}
        return ClaimedExecutionCommand(
            owner_ref=str(row[0]),
            execution_id=str(row[1]),
            command=command if isinstance(command, dict) else {},
            attempt=int(row[3]) + 1,
            revision=int(row[4]) + 1,
        )

    def claim_reconcile(
        self, *, worker_id: str, lease_seconds: int = 30
    ) -> ClaimedReconcileCommand | None:
        """Lease one due reconcile row without exposing it to dispatch."""
        now = self._clock()
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_ref, execution_id, command_json, attempt, "
                "reconcile_attempt, reconcile_redispatch_count, revision "
                "FROM execution_commands_v2 WHERE state = 'reconcile' "
                "AND (next_reconcile_at IS NULL OR next_reconcile_at <= ?) "
                "AND (lease_owner IS NULL OR lease_expires_at < ?) "
                "ORDER BY COALESCE(next_reconcile_at, created_at), "
                "created_at, execution_id LIMIT 1",
                (now_text, now_text),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            result = connection.execute(
                "UPDATE execution_commands_v2 SET reconcile_attempt = "
                "reconcile_attempt + 1, lease_owner = ?, "
                "lease_expires_at = ?, updated_at = ?, "
                "revision = revision + 1 WHERE owner_ref = ? "
                "AND execution_id = ? AND state = 'reconcile' "
                "AND revision = ?",
                (
                    worker_id,
                    lease_text,
                    now_text,
                    row[0],
                    row[1],
                    row[6],
                ),
            )
            connection.commit()
            if result.rowcount != 1:
                return None
        try:
            command = json.loads(str(row[2]))
        except (TypeError, ValueError):
            command = {}
        return ClaimedReconcileCommand(
            owner_ref=str(row[0]),
            execution_id=str(row[1]),
            command=command if isinstance(command, dict) else {},
            attempt=int(row[3]),
            reconcile_attempt=int(row[4]) + 1,
            redispatch_count=int(row[5]),
            revision=int(row[6]) + 1,
            lease_owner=worker_id,
        )

    def resolve_reconcile(self, claim: ClaimedReconcileCommand) -> bool:
        """Acknowledge a reconcile command after authoritative evidence."""
        return self._settle_reconcile(
            claim,
            state="acknowledged",
            code="reconcile_resolved",
            next_reconcile_at=None,
        )

    def reschedule_reconcile(
        self,
        claim: ClaimedReconcileCommand,
        *,
        code: str,
        delay_seconds: int,
    ) -> bool:
        """Retain ambiguous work with finite diagnostic backoff."""
        if delay_seconds < 1 or delay_seconds > 300:
            raise ValueError("invalid reconcile delay")
        return self._settle_reconcile(
            claim,
            state="reconcile",
            code=code,
            next_reconcile_at=(
                self._clock() + timedelta(seconds=delay_seconds)
            ).isoformat(),
        )

    def redispatch_reconcile(self, claim: ClaimedReconcileCommand) -> bool:
        """Schedule the single allowed safe replay of an unstarted command."""
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            result = connection.execute(
                "UPDATE execution_commands_v2 SET state = 'retry', "
                "next_attempt_at = ?, next_reconcile_at = NULL, "
                "lease_owner = NULL, lease_expires_at = NULL, "
                "classification = 'retry', "
                "last_error_code = 'reconcile_safe_redispatch', "
                "reconcile_redispatch_count = reconcile_redispatch_count + 1, "
                "updated_at = ?, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND state = 'reconcile' AND lease_owner = ? "
                "AND revision = ? AND reconcile_redispatch_count = 0",
                (
                    self._clock().isoformat(),
                    self._clock().isoformat(),
                    claim.owner_ref,
                    claim.execution_id,
                    claim.lease_owner,
                    claim.revision,
                ),
            )
            connection.commit()
            return result.rowcount == 1

    def release_failed_turn_and_redispatch(
        self,
        claim: ClaimedReconcileCommand,
        *,
        conversation_key: str,
        turn_id: str,
        allow_routed_binding: bool = False,
    ) -> bool:
        """Atomically release only the matching failed-unstarted turn."""
        command = claim.command
        arguments = command.get("arguments")
        conversation = (
            arguments.get("__conversation")
            if isinstance(arguments, dict)
            else None
        )
        agent = command.get("agent")
        if (
            command.get("owner_ref") != claim.owner_ref
            or command.get("execution_id") != claim.execution_id
            or not isinstance(agent, str)
            or not isinstance(arguments, dict)
            or not isinstance(conversation, dict)
            or conversation.get("conversation_key") != conversation_key
            or str(conversation.get("turn_id")) != turn_id
        ):
            return False
        expected_hash = execution_command_hash(
            ExecutionCommand(agent_slug=agent, arguments=arguments)
        )
        now = self._clock().isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                "SELECT status, execution_command_hash, agent, "
                "execution_fingerprint_version, execution_fingerprint, "
                "execution_terminal_outcome FROM runs "
                "WHERE user_id = ? AND execution_id = ? "
                "AND execution_tombstoned_at IS NULL",
                (claim.owner_ref, claim.execution_id),
            ).fetchone()
            direct_match = (
                reservation is not None
                and reservation[0] == "admitted"
                and reservation[1] == expected_hash
                and reservation[5] is None
            )
            routed_match = (
                allow_routed_binding
                and reservation is not None
                and reservation[0] == "admitted"
                and reservation[5] is None
                and routed_binding_matches_command(
                    command,
                    owner=claim.owner_ref,
                    execution_id=claim.execution_id,
                    reservation_agent=str(reservation[2]),
                    reservation_command_hash=str(reservation[1]),
                    fingerprint_version=reservation[3],
                    fingerprint=reservation[4],
                )
            )
            if not (direct_match or routed_match):
                connection.rollback()
                return False
            projection = connection.execute(
                "SELECT latest_seq, projection_json "
                "FROM execution_projection_v2 WHERE owner_ref = ? "
                "AND execution_id = ?",
                (claim.owner_ref, claim.execution_id),
            ).fetchone()
            projection_terminal = False
            if projection is not None and isinstance(projection[1], str):
                try:
                    projection_value = json.loads(projection[1])
                except (TypeError, ValueError):
                    projection_value = None
                projection_terminal = (
                    isinstance(projection_value, dict)
                    and projection_value.get("terminal") is not None
                )
            if projection is not None and (
                int(projection[0]) > 0 or projection_terminal
            ):
                connection.rollback()
                return False
            tables = (
                "execution_events_v2",
                "execution_spans",
                "execution_work_units",
                "execution_target_bindings_v2",
                "execution_operations_v2",
            )
            for table in tables:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE owner_ref = ? "  # noqa: S608
                    "AND execution_id = ?",
                    (claim.owner_ref, claim.execution_id),
                ).fetchone()[0]
                if count:
                    connection.rollback()
                    return False
            released = connection.execute(
                "DELETE FROM conversation_turns WHERE conversation_key = ? "
                "AND turn_id = ? AND state = 'failed' "
                "AND result_json IS NULL AND delta_json IS NULL "
                "AND ledger_version IS NULL",
                (conversation_key, turn_id),
            )
            if released.rowcount != 1:
                connection.rollback()
                return False
            scheduled = connection.execute(
                "UPDATE execution_commands_v2 SET state = 'retry', "
                "next_attempt_at = ?, next_reconcile_at = NULL, "
                "lease_owner = NULL, lease_expires_at = NULL, "
                "classification = 'retry', "
                "last_error_code = 'reconcile_safe_redispatch', "
                "reconcile_redispatch_count = 1, updated_at = ?, "
                "revision = revision + 1 WHERE owner_ref = ? "
                "AND execution_id = ? AND state = 'reconcile' "
                "AND lease_owner = ? AND revision = ? "
                "AND reconcile_redispatch_count = 0",
                (
                    now,
                    now,
                    claim.owner_ref,
                    claim.execution_id,
                    claim.lease_owner,
                    claim.revision,
                ),
            )
            if scheduled.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True

    def _settle_reconcile(
        self,
        claim: ClaimedReconcileCommand,
        *,
        state: str,
        code: str,
        next_reconcile_at: str | None,
    ) -> bool:
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            result = connection.execute(
                "UPDATE execution_commands_v2 SET state = ?, "
                "next_reconcile_at = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, last_error_code = ?, "
                "updated_at = ?, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND state = 'reconcile' AND lease_owner = ? "
                "AND revision = ?",
                (
                    state,
                    next_reconcile_at,
                    code,
                    self._clock().isoformat(),
                    claim.owner_ref,
                    claim.execution_id,
                    claim.lease_owner,
                    claim.revision,
                ),
            )
            connection.commit()
            return result.rowcount == 1

    def acknowledge(self, claim: ClaimedExecutionCommand) -> bool:
        return self._settle(
            claim,
            state="acknowledged",
            classification=None,
            boundary_state=None,
            error_code=None,
        )

    def retry(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary = InvocationBoundary.ENTERED,
    ) -> bool:
        delay = min(60, 2 ** min(claim.attempt, 5))
        return self._settle(
            claim,
            state="retry",
            classification=DispatchClassification.RETRY,
            boundary_state=boundary_state,
            error_code=code,
            next_attempt_at=(
                self._clock() + timedelta(seconds=delay)
            ).isoformat(),
        )

    def reconcile(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary = InvocationBoundary.ENTERED,
    ) -> bool:
        now = self._clock().isoformat()
        return self._settle(
            claim,
            state="reconcile",
            classification=DispatchClassification.RECONCILE,
            boundary_state=boundary_state,
            error_code=code,
            next_reconcile_at=now,
        )

    def reject(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary,
    ) -> bool:
        return self._settle(
            claim,
            state="rejected",
            classification=DispatchClassification.REJECT,
            boundary_state=boundary_state,
            error_code=code,
        )

    def dead_letter(
        self, claim: ClaimedExecutionCommand, *, code: str
    ) -> bool:
        """Retain the legacy operator-only state outside normal dispatch."""
        return self._settle(
            claim,
            state="dead_letter",
            classification=None,
            boundary_state=None,
            error_code=code,
        )

    def _settle(
        self,
        claim: ClaimedExecutionCommand,
        *,
        state: str,
        classification: DispatchClassification | None,
        boundary_state: InvocationBoundary | None,
        error_code: str | None,
        next_attempt_at: str | None = None,
        next_reconcile_at: str | None = None,
    ) -> bool:
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            result = connection.execute(
                "UPDATE execution_commands_v2 SET state = ?, next_attempt_at = ?, "
                "next_reconcile_at = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, classification = ?, "
                "boundary_state = ?, "
                "first_error_code = COALESCE(first_error_code, ?), "
                "last_error_code = COALESCE(?, last_error_code), "
                "updated_at = ?, revision = revision + 1 WHERE owner_ref = ? "
                "AND execution_id = ? AND state = 'processing' AND revision = ?",
                (
                    state,
                    next_attempt_at,
                    next_reconcile_at,
                    (
                        classification.value
                        if classification is not None
                        else None
                    ),
                    (
                        boundary_state.value
                        if boundary_state is not None
                        else None
                    ),
                    error_code,
                    error_code,
                    self._clock().isoformat(),
                    claim.owner_ref,
                    claim.execution_id,
                    claim.revision,
                ),
            )
            connection.commit()
            return result.rowcount == 1


def _validated_command(
    claim: ClaimedExecutionCommand,
) -> tuple[str, str, dict[str, Any], int, str]:
    command = claim.command
    required = {
        "agent",
        "arguments",
        "execution_id",
        "owner_ref",
        "fingerprint_version",
        "fingerprint",
    }
    if set(command) != required:
        raise ValueError("invalid_command_shape")
    agent = command.get("agent")
    arguments = command.get("arguments")
    execution_id = command.get("execution_id")
    owner_ref = command.get("owner_ref")
    version = command.get("fingerprint_version")
    fingerprint = command.get("fingerprint")
    if (
        not isinstance(agent, str)
        or (
            public_agent_spec(agent) is None
            and agent != EXPERT_ROUTER_AGENT_SLUG
        )
        or not isinstance(arguments, dict)
        or execution_id != claim.execution_id
        or owner_ref != claim.owner_ref
        or not isinstance(version, int)
        or version < 1
        or not isinstance(fingerprint, str)
        or len(fingerprint) < 32
    ):
        raise ValueError("invalid_command_contract")
    if agent == EXPERT_ROUTER_AGENT_SLUG:
        return "ExpertRouter", agent, arguments, version, fingerprint
    spec = public_agent_spec(agent)
    assert spec is not None
    return spec.tool, agent, arguments, version, fingerprint


def _terminalize_admitted_dispatch_failure(
    *, db_path: str, claim: ClaimedExecutionCommand, code: str
) -> bool:
    """Delegate poison-command terminalization to the fenced repository."""
    reservations = SQLiteExecutionReservationRepository(db_path)
    record = reservations.get(
        owner=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    if record.status is not ExecutionStatus.ADMITTED:
        return record.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.PARTIAL,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    outcome = DriverOutcome.failed(code=code)
    settled = reservations.settle_terminal(
        TerminalSettlementAuthority(
            owner_ref=claim.owner_ref,
            execution_id=claim.execution_id,
            expected_revision=record.supervisor_revision,
            actor="supervisor",
            issued_at=datetime.now(UTC),
        ),
        outcome,
    )
    if settled:
        return True
    current = reservations.get(
        owner=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    return current.status in {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.PARTIAL,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }


def _classified_dispatch_failure(
    exc: Exception,
    *,
    boundary_state: InvocationBoundary,
) -> ClassifiedDispatchFailure:
    """Map typed errors to finite safe delivery semantics."""
    if isinstance(exc, McpError):
        nested = getattr(getattr(exc, "error", None), "code", None)
        if nested == INVALID_PARAMS:
            return ClassifiedDispatchFailure(
                DispatchClassification.REJECT,
                "mcp_invalid_params",
                boundary_state,
            )

    candidate = getattr(exc, "code", None)
    safe_code = (
        candidate
        if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate)
        else None
    )
    if safe_code in {
        "conversation_context_turn_in_progress",
        "execution_already_reserved",
    }:
        return ClassifiedDispatchFailure(
            DispatchClassification.RECONCILE,
            safe_code,
            InvocationBoundary.DURABLY_CLAIMED,
        )
    if safe_code is not None and getattr(exc, "retryable", None) is False:
        return ClassifiedDispatchFailure(
            DispatchClassification.REJECT,
            safe_code,
            boundary_state,
        )
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return ClassifiedDispatchFailure(
            DispatchClassification.RETRY,
            "provider_rate_limited",
            boundary_state,
        )
    if isinstance(status_code, int) and status_code >= 500:
        return ClassifiedDispatchFailure(
            DispatchClassification.RETRY,
            "provider_unavailable",
            boundary_state,
        )
    if isinstance(exc, (ConnectionError, OSError)):
        return ClassifiedDispatchFailure(
            DispatchClassification.RETRY,
            "transport_connection_failed",
            boundary_state,
        )
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return ClassifiedDispatchFailure(
            DispatchClassification.RETRY,
            "transport_timeout",
            boundary_state,
        )
    return ClassifiedDispatchFailure(
        (
            DispatchClassification.RETRY
            if boundary_state is InvocationBoundary.NOT_ENTERED
            else DispatchClassification.RECONCILE
        ),
        (
            "dispatch_unknown_before_boundary"
            if boundary_state is InvocationBoundary.NOT_ENTERED
            else "dispatch_unknown_after_boundary"
        ),
        boundary_state,
    )


async def dispatch_one_execution_command(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    worker_id: str,
    db_path: str,
    invoke: InvokeCommand,
) -> bool:
    claim = await asyncio.to_thread(queue.claim, worker_id=worker_id)
    if claim is None:
        return False
    try:
        tool, agent, arguments, version, fingerprint = _validated_command(
            claim
        )
    except ValueError as exc:
        settled = await asyncio.to_thread(
            _terminalize_admitted_dispatch_failure,
            db_path=db_path,
            claim=claim,
            code="invalid_command",
        )
        if settled:
            await asyncio.to_thread(
                queue.reject,
                claim,
                code="invalid_command",
                boundary_state=InvocationBoundary.NOT_ENTERED,
            )
        else:
            await asyncio.to_thread(
                queue.reconcile,
                claim,
                code="terminal_settlement_pending",
                boundary_state=InvocationBoundary.NOT_ENTERED,
            )
        del exc
        return True
    try:
        with request_context(
            claim.owner_ref,
            f"dispatch:{claim.execution_id}",
        ):
            await invoke(
                tool,
                arguments,
                execution_id=claim.execution_id,
                transport="service_dispatcher",
                db_path=db_path,
                agent_slug=agent,
                fingerprint_version=version,
                fingerprint=fingerprint,
            )
    except asyncio.CancelledError:
        await asyncio.to_thread(
            queue.reconcile,
            claim,
            code="dispatch_unknown_after_boundary",
            boundary_state=InvocationBoundary.ENTERED,
        )
        raise
    except Exception as exc:  # private exception text is intentionally dropped
        failure = _classified_dispatch_failure(
            exc,
            boundary_state=InvocationBoundary.ENTERED,
        )
        try:
            record = SQLiteExecutionReservationRepository(db_path).get(
                owner=claim.owner_ref,
                execution_id=claim.execution_id,
            )
        except Exception:
            record = None
        # Once Runtime has claimed the reservation, the detached command has
        # been delivered.  Any later response/projection failure belongs to
        # the execution supervisor; replaying the start command would compete
        # with the already-running business execution.
        if (
            record is not None
            and record.status is not ExecutionStatus.ADMITTED
        ):
            _LOGGER.warning(
                "execution command response failed after runtime claim "
                "code=%s attempt=%s status=%s",
                failure.code,
                claim.attempt,
                record.status.value,
            )
            await asyncio.to_thread(queue.acknowledge, claim)
            return True
        _LOGGER.warning(
            "execution command dispatch failed code=%s attempt=%s "
            "classification=%s boundary=%s error_type=%s",
            failure.code,
            claim.attempt,
            failure.classification.value,
            failure.boundary_state.value,
            type(exc).__name__,
        )
        if failure.classification is DispatchClassification.REJECT:
            settled = await asyncio.to_thread(
                _terminalize_admitted_dispatch_failure,
                db_path=db_path,
                claim=claim,
                code=failure.code,
            )
            if settled:
                await asyncio.to_thread(
                    queue.reject,
                    claim,
                    code=failure.code,
                    boundary_state=failure.boundary_state,
                )
            else:
                await asyncio.to_thread(
                    queue.reconcile,
                    claim,
                    code="terminal_settlement_pending",
                    boundary_state=failure.boundary_state,
                )
        elif (
            failure.classification is DispatchClassification.RECONCILE
            or claim.attempt >= 5
        ):
            await asyncio.to_thread(
                queue.reconcile,
                claim,
                code=failure.code,
                boundary_state=failure.boundary_state,
            )
        else:
            await asyncio.to_thread(
                queue.retry,
                claim,
                code=failure.code,
                boundary_state=failure.boundary_state,
            )
        return True
    await asyncio.to_thread(queue.acknowledge, claim)
    return True


async def run_execution_command_dispatcher(
    *,
    db_path: str,
    invoke: InvokeCommand,
    stop: asyncio.Event,
    poll_seconds: float = 0.25,
) -> None:
    # The worker starts before the first service admission on a fresh install.
    # Bootstrap the canonical V2 schema here so its first empty claim cannot
    # terminate the process-lifetime task with ``no such table``.
    SQLiteExecutionReservationRepository(db_path)
    queue = SQLiteExecutionCommandQueueV2(db_path)
    worker_id = IdFactory().new_id("worker", "bot")
    while not stop.is_set():
        worked = await dispatch_one_execution_command(
            queue=queue,
            worker_id=worker_id,
            db_path=db_path,
            invoke=invoke,
        )
        if not worked:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


__all__ = [
    "ClaimedExecutionCommand",
    "ClaimedReconcileCommand",
    "SQLiteExecutionCommandQueueV2",
    "dispatch_one_execution_command",
    "run_execution_command_dispatcher",
]
