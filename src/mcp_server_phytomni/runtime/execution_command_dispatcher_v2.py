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
from .execution_command_support_v2 import (
    ValidatedCommand,
    command_identity,
    matches_conversation_turn,
    projection_has_terminal,
    supervisor_settlement_authority,
)
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    SQLiteExecutionReservationRepository,
    execution_command_hash,
    routed_binding_matches_command,
)
from .execution_runtime_contracts import (
    TERMINAL_EXECUTION_STATUSES,
    DriverOutcome,
    ExecutionCommand,
)
from .request_context import request_context
from .sqlite import sqlite_transaction

InvokeCommand = Callable[..., Awaitable[Any]]
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_LOGGER = logging.getLogger(__name__)
_UNSTARTED_EVIDENCE_QUERIES = (
    "SELECT COUNT(*) FROM execution_events_v2 WHERE owner_ref = ? "
    "AND execution_id = ?",
    "SELECT COUNT(*) FROM execution_spans WHERE owner_ref = ? "
    "AND execution_id = ?",
    "SELECT COUNT(*) FROM execution_work_units WHERE owner_ref = ? "
    "AND execution_id = ?",
    "SELECT COUNT(*) FROM execution_target_bindings_v2 WHERE owner_ref = ? "
    "AND execution_id = ?",
    "SELECT COUNT(*) FROM execution_operations_v2 WHERE owner_ref = ? "
    "AND execution_id = ?",
)


@dataclass(frozen=True, slots=True)
class ClaimedExecutionCommand:
    """One detached execution command held under an optimistic lease."""

    owner_ref: str
    execution_id: str
    command: dict[str, Any]
    attempt: int
    revision: int


@dataclass(frozen=True, slots=True)
class _ClaimedCommandIdentity:
    """Durable identity shared by dispatch and reconcile command claims."""

    owner_ref: str
    execution_id: str
    command: dict[str, Any]
    attempt: int


@dataclass(frozen=True, slots=True)
class ClaimedReconcileCommand(_ClaimedCommandIdentity):
    """A reconcile row leased without entering normal processing state."""

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
    """Stable retry decision with its invocation-side-effect boundary."""

    classification: DispatchClassification
    code: str
    boundary_state: InvocationBoundary


@dataclass(frozen=True, slots=True)
class _CommandSettlement:
    """One fenced state transition for a processing command."""

    state: str
    classification: DispatchClassification | None = None
    boundary_state: InvocationBoundary | None = None
    error_code: str | None = None
    next_attempt_at: str | None = None
    next_reconcile_at: str | None = None


class SQLiteExecutionCommandQueueV2:
    """Lease-based command queue sharing the execution registry database."""

    def __init__(self, db_path: str, *, clock=None) -> None:
        self.db_path = db_path
        self._clock = clock or (lambda: datetime.now(UTC))

    def claim(
        self, *, worker_id: str, lease_seconds: int = 30
    ) -> ClaimedExecutionCommand | None:
        """Lease the oldest due command for one dispatcher worker."""
        now = self._clock()
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_ref, execution_id, command_json, "
                "attempt, revision "
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
                "attempt = attempt + 1, lease_owner = ?, "
                "lease_expires_at = ?, "
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
        with sqlite_transaction(self.db_path, timeout=10) as connection:
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
        with sqlite_transaction(self.db_path, timeout=10) as connection:
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
        identity = command_identity(
            claim.command,
            owner_ref=claim.owner_ref,
            execution_id=claim.execution_id,
        )
        if identity is None or not matches_conversation_turn(
            claim.command,
            conversation_key=conversation_key,
            turn_id=turn_id,
        ):
            return False
        expected_hash = execution_command_hash(
            ExecutionCommand(
                agent_slug=identity.agent,
                arguments=identity.arguments,
            )
        )
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            if (
                not _reconcile_binding_matches(
                    connection,
                    claim,
                    expected_hash=expected_hash,
                    allow_routed_binding=allow_routed_binding,
                )
                or _execution_has_started(connection, claim)
                or not _release_turn_and_schedule_redispatch(
                    connection,
                    claim,
                    conversation_key=conversation_key,
                    turn_id=turn_id,
                    now=now,
                )
            ):
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
        with sqlite_transaction(self.db_path, timeout=10) as connection:
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
        """Mark a leased command as successfully delivered."""
        return self._settle(
            claim,
            _CommandSettlement(state="acknowledged"),
        )

    def retry(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary = InvocationBoundary.ENTERED,
    ) -> bool:
        """Release a failed claim into bounded exponential retry."""
        delay = min(60, 2 ** min(claim.attempt, 5))
        return self._settle(
            claim,
            _CommandSettlement(
                state="retry",
                classification=DispatchClassification.RETRY,
                boundary_state=boundary_state,
                error_code=code,
                next_attempt_at=(
                    self._clock() + timedelta(seconds=delay)
                ).isoformat(),
            ),
        )

    def reconcile(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary = InvocationBoundary.ENTERED,
    ) -> bool:
        """Move an uncertain claim to evidence-based reconciliation."""
        now = self._clock().isoformat()
        return self._settle(
            claim,
            _CommandSettlement(
                state="reconcile",
                classification=DispatchClassification.RECONCILE,
                boundary_state=boundary_state,
                error_code=code,
                next_reconcile_at=now,
            ),
        )

    def reject(
        self,
        claim: ClaimedExecutionCommand,
        *,
        code: str,
        boundary_state: InvocationBoundary,
    ) -> bool:
        """Reject a deterministically invalid command at its boundary."""
        return self._settle(
            claim,
            _CommandSettlement(
                state="rejected",
                classification=DispatchClassification.REJECT,
                boundary_state=boundary_state,
                error_code=code,
            ),
        )

    def dead_letter(
        self, claim: ClaimedExecutionCommand, *, code: str
    ) -> bool:
        """Retain the legacy operator-only state outside normal dispatch."""
        return self._settle(
            claim,
            _CommandSettlement(
                state="dead_letter",
                error_code=code,
            ),
        )

    def _settle(
        self,
        claim: ClaimedExecutionCommand,
        settlement: _CommandSettlement,
    ) -> bool:
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            result = connection.execute(
                "UPDATE execution_commands_v2 SET state = ?, "
                "next_attempt_at = ?, "
                "next_reconcile_at = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, classification = ?, "
                "boundary_state = ?, "
                "first_error_code = COALESCE(first_error_code, ?), "
                "last_error_code = COALESCE(?, last_error_code), "
                "updated_at = ?, revision = revision + 1 WHERE owner_ref = ? "
                "AND execution_id = ? AND state = 'processing' "
                "AND revision = ?",
                (
                    settlement.state,
                    settlement.next_attempt_at,
                    settlement.next_reconcile_at,
                    (
                        settlement.classification.value
                        if settlement.classification is not None
                        else None
                    ),
                    (
                        settlement.boundary_state.value
                        if settlement.boundary_state is not None
                        else None
                    ),
                    settlement.error_code,
                    settlement.error_code,
                    self._clock().isoformat(),
                    claim.owner_ref,
                    claim.execution_id,
                    claim.revision,
                ),
            )
            connection.commit()
            return result.rowcount == 1


def _reconcile_binding_matches(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
    *,
    expected_hash: str,
    allow_routed_binding: bool,
) -> bool:
    reservation = connection.execute(
        "SELECT status, execution_command_hash, agent, "
        "execution_fingerprint_version, execution_fingerprint, "
        "execution_terminal_outcome FROM runs "
        "WHERE user_id = ? AND execution_id = ? "
        "AND execution_tombstoned_at IS NULL",
        (claim.owner_ref, claim.execution_id),
    ).fetchone()
    if reservation is None:
        return False
    if reservation[0] != "admitted" or reservation[5] is not None:
        return False
    if reservation[1] == expected_hash:
        return True
    return allow_routed_binding and routed_binding_matches_command(
        claim.command,
        owner=claim.owner_ref,
        execution_id=claim.execution_id,
        reservation_agent=str(reservation[2]),
        reservation_command_hash=str(reservation[1]),
        fingerprint_version=reservation[3],
        fingerprint=reservation[4],
    )


def _execution_has_started(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
) -> bool:
    projection = connection.execute(
        "SELECT latest_seq, projection_json "
        "FROM execution_projection_v2 WHERE owner_ref = ? "
        "AND execution_id = ?",
        (claim.owner_ref, claim.execution_id),
    ).fetchone()
    if projection is not None and (
        int(projection[0]) > 0 or projection_has_terminal(projection[1])
    ):
        return True
    return any(
        connection.execute(
            query,
            (claim.owner_ref, claim.execution_id),
        ).fetchone()[0]
        for query in _UNSTARTED_EVIDENCE_QUERIES
    )


def _release_turn_and_schedule_redispatch(
    connection: sqlite3.Connection,
    claim: ClaimedReconcileCommand,
    *,
    conversation_key: str,
    turn_id: str,
    now: str,
) -> bool:
    released = connection.execute(
        "DELETE FROM conversation_turns WHERE conversation_key = ? "
        "AND turn_id = ? AND state = 'failed' "
        "AND result_json IS NULL AND delta_json IS NULL "
        "AND ledger_version IS NULL",
        (conversation_key, turn_id),
    )
    if released.rowcount != 1:
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
    return scheduled.rowcount == 1


def _validated_command(
    claim: ClaimedExecutionCommand,
) -> ValidatedCommand:
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
    identity = command_identity(
        command,
        owner_ref=claim.owner_ref,
        execution_id=claim.execution_id,
    )
    version = command.get("fingerprint_version")
    fingerprint = command.get("fingerprint")
    if identity is None or not _valid_fingerprint(version, fingerprint):
        raise ValueError("invalid_command_contract")
    tool = _agent_tool(identity.agent)
    if tool is None:
        raise ValueError("invalid_command_contract")
    assert isinstance(version, int)
    assert isinstance(fingerprint, str)
    return ValidatedCommand(
        tool=tool,
        agent=identity.agent,
        arguments=identity.arguments,
        fingerprint_version=version,
        fingerprint=fingerprint,
    )


def _valid_fingerprint(version: object, fingerprint: object) -> bool:
    return (
        isinstance(version, int)
        and version >= 1
        and isinstance(fingerprint, str)
        and len(fingerprint) >= 32
    )


def _agent_tool(agent: str) -> str | None:
    if agent == EXPERT_ROUTER_AGENT_SLUG:
        return "ExpertRouter"
    spec = public_agent_spec(agent)
    return spec.tool if spec is not None else None


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
        return record.status in TERMINAL_EXECUTION_STATUSES
    outcome = DriverOutcome.failed(code=code)
    settled = reservations.settle_terminal(
        supervisor_settlement_authority(
            owner_ref=claim.owner_ref,
            execution_id=claim.execution_id,
            expected_revision=record.supervisor_revision,
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
    return current.status in TERMINAL_EXECUTION_STATUSES


def _classified_dispatch_failure(
    exc: Exception,
    *,
    boundary_state: InvocationBoundary,
) -> ClassifiedDispatchFailure:
    """Map typed errors to finite safe delivery semantics."""
    classification = DispatchClassification.RECONCILE
    code = "dispatch_unknown_after_boundary"
    classified_boundary = boundary_state
    nested_mcp_code = (
        getattr(getattr(exc, "error", None), "code", None)
        if isinstance(exc, McpError)
        else None
    )
    if nested_mcp_code == INVALID_PARAMS:
        classification = DispatchClassification.REJECT
        code = "mcp_invalid_params"
    else:
        safe_code = _safe_error_code(exc)
        status_code = getattr(exc, "status_code", None)
        if safe_code in {
            "conversation_context_turn_in_progress",
            "execution_already_reserved",
        }:
            code = safe_code
            classified_boundary = InvocationBoundary.DURABLY_CLAIMED
        elif (
            safe_code is not None and getattr(exc, "retryable", None) is False
        ):
            classification = DispatchClassification.REJECT
            code = safe_code
        elif status_code == 429:
            classification = DispatchClassification.RETRY
            code = "provider_rate_limited"
        elif isinstance(status_code, int) and status_code >= 500:
            classification = DispatchClassification.RETRY
            code = "provider_unavailable"
        elif isinstance(exc, (ConnectionError, OSError)):
            classification = DispatchClassification.RETRY
            code = "transport_connection_failed"
        elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            classification = DispatchClassification.RETRY
            code = "transport_timeout"
        elif boundary_state is InvocationBoundary.NOT_ENTERED:
            classification = DispatchClassification.RETRY
            code = "dispatch_unknown_before_boundary"
    return ClassifiedDispatchFailure(
        classification,
        code,
        classified_boundary,
    )


def _safe_error_code(exc: Exception) -> str | None:
    candidate = getattr(exc, "code", None)
    if isinstance(candidate, str) and _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return None


async def _invoke_capturing_failure(
    *,
    invoke: InvokeCommand,
    command: ValidatedCommand,
    claim: ClaimedExecutionCommand,
    db_path: str,
) -> BaseException | None:
    async def invoke_command() -> None:
        with request_context(
            claim.owner_ref,
            f"dispatch:{claim.execution_id}",
        ):
            await invoke(
                command.tool,
                command.arguments,
                execution_id=claim.execution_id,
                transport="service_dispatcher",
                db_path=db_path,
                agent_slug=command.agent,
                fingerprint_version=command.fingerprint_version,
                fingerprint=command.fingerprint,
            )

    outcome = (await asyncio.gather(invoke_command(), return_exceptions=True))[
        0
    ]
    return outcome if isinstance(outcome, BaseException) else None


async def _reconcile_cancelled_dispatch(
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedExecutionCommand,
) -> None:
    await asyncio.to_thread(
        queue.reconcile,
        claim,
        code="dispatch_unknown_after_boundary",
        boundary_state=InvocationBoundary.ENTERED,
    )


async def _reject_invalid_command(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedExecutionCommand,
    db_path: str,
) -> None:
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
        return
    await asyncio.to_thread(
        queue.reconcile,
        claim,
        code="terminal_settlement_pending",
        boundary_state=InvocationBoundary.NOT_ENTERED,
    )


async def _settle_rejected_dispatch_failure(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedExecutionCommand,
    db_path: str,
    failure: ClassifiedDispatchFailure,
) -> None:
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
        return
    await asyncio.to_thread(
        queue.reconcile,
        claim,
        code="terminal_settlement_pending",
        boundary_state=failure.boundary_state,
    )


async def _settle_dispatch_failure(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    claim: ClaimedExecutionCommand,
    db_path: str,
    exc: Exception,
) -> None:
    failure = _classified_dispatch_failure(
        exc,
        boundary_state=InvocationBoundary.ENTERED,
    )
    record = None
    with suppress(Exception):
        record = SQLiteExecutionReservationRepository(db_path).get(
            owner=claim.owner_ref,
            execution_id=claim.execution_id,
        )
    # Once Runtime has claimed the reservation, the detached command has
    # been delivered. Any later response failure belongs to the supervisor.
    if record is not None and record.status is not ExecutionStatus.ADMITTED:
        _LOGGER.warning(
            "execution command response failed after runtime claim "
            "code=%s attempt=%s status=%s",
            failure.code,
            claim.attempt,
            record.status.value,
        )
        await asyncio.to_thread(queue.acknowledge, claim)
        return
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
        await _settle_rejected_dispatch_failure(
            queue=queue,
            claim=claim,
            db_path=db_path,
            failure=failure,
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


async def dispatch_one_execution_command(
    *,
    queue: SQLiteExecutionCommandQueueV2,
    worker_id: str,
    db_path: str,
    invoke: InvokeCommand,
) -> bool:
    """Claim, validate, and deliver at most one detached command."""
    claim = await asyncio.to_thread(queue.claim, worker_id=worker_id)
    if claim is None:
        return False
    try:
        command = _validated_command(claim)
    except ValueError:
        await _reject_invalid_command(
            queue=queue,
            claim=claim,
            db_path=db_path,
        )
        return True
    try:
        failure = await _invoke_capturing_failure(
            invoke=invoke,
            command=command,
            claim=claim,
            db_path=db_path,
        )
    except asyncio.CancelledError:
        await _reconcile_cancelled_dispatch(queue, claim)
        raise
    if isinstance(failure, asyncio.CancelledError):
        await _reconcile_cancelled_dispatch(queue, claim)
        raise failure
    if isinstance(failure, Exception):
        await _settle_dispatch_failure(
            queue=queue,
            claim=claim,
            db_path=db_path,
            exc=failure,
        )
    elif failure is not None:
        raise failure
    else:
        await asyncio.to_thread(queue.acknowledge, claim)
    return True


async def run_execution_command_dispatcher(
    *,
    db_path: str,
    invoke: InvokeCommand,
    stop: asyncio.Event,
    poll_seconds: float = 0.25,
) -> None:
    """Poll and deliver detached commands until shutdown is requested."""
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
