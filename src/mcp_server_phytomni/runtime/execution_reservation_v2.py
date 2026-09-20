# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Idempotent owner/execution reservation before Agent business execution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ..config.defaults import ApiConfig
from ..public_agent_catalog import Driver, public_agent_spec
from ..storage.path_policy import IdFactory
from .conversation_context.models import ContextStageMetadata
from .execution_journal_store_v2 import SQLiteExecutionJournal
from .execution_journal_v2 import (
    EventStatus,
    ExecutionEventIntentV2,
    ExecutionEventType,
    ExecutionStatus,
    parse_execution_event_intent_v2,
)
from .execution_runtime_contracts import (
    CancellationOutcome,
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    TerminalSettlementAuthority,
)
from .sqlite import sqlite_transaction

# Private admission-only identity for autonomous Expert routing.  It is not a
# public Agent and must never be exported by the canonical Agent catalog.
EXPERT_ROUTER_AGENT_SLUG = "expert-router"


class ExecutionReservationConflictError(RuntimeError):
    """The owner/execution key was already bound to different input."""


class ExecutionReservationNotFoundError(LookupError):
    """The owner-scoped execution binding does not exist."""


@dataclass(frozen=True, slots=True)
class ExecutionReservationRecord:
    """Private Bot binding below the stable public execution identity."""

    owner: str
    execution_id: str
    run_id: str
    fingerprint_version: int
    fingerprint: str
    command_hash: str
    agent_slug: str
    driver: Driver
    root_span_id: str
    status: ExecutionStatus
    deadline_at: str
    supervisor_revision: int
    next_attempt_at: str | None
    tracking_health: str
    cancellation_state: CancellationOutcome
    context_stage_json: str | None


@dataclass(frozen=True, slots=True)
class ExecutionOperationClaim:
    """Durable idempotency claim for resume/recovery/control operations."""

    operation_id: str
    operation: str
    expected_revision: int
    command_hash: str
    claimed: bool
    state: str
    outcome_json: str | None
    supervisor_revision: int


class SQLiteExecutionReservationRepository:
    """Transactional execution/root-run binding in the shared registry DB."""

    def __init__(
        self,
        db_path: str,
        *,
        run_id_factory: Callable[[], str] | None = None,
        root_span_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_provider_join_lease_token: str | None = None,
    ) -> None:
        from .run_registry import RunRegistry

        self.db_path = db_path
        self._run_id_factory = run_id_factory
        self._root_span_id_factory = root_span_id_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        if expected_provider_join_lease_token is not None and (
            not expected_provider_join_lease_token
            or len(expected_provider_join_lease_token) > 128
        ):
            raise ValueError("invalid provider join lease token")
        self._expected_provider_join_lease_token = (
            expected_provider_join_lease_token
        )
        RunRegistry(db_path)
        self._journal = SQLiteExecutionJournal(
            db_path,
            clock=lambda: self._clock().isoformat(),
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        )

    def reserve(
        self,
        *,
        owner: str,
        execution_id: str,
        fingerprint_version: int,
        fingerprint: str,
        command: ExecutionCommand,
        durable_command: Mapping[str, Any] | None = None,
    ) -> ExecutionReservationRecord:
        """Reserve once; exact retries return the original binding."""
        if not owner or not execution_id or not fingerprint:
            raise ValueError(
                "owner, execution_id, and fingerprint are required"
            )
        if fingerprint_version < 1 or len(fingerprint) > 256:
            raise ValueError("invalid fingerprint")
        spec = public_agent_spec(command.agent_slug)
        if spec is None and command.agent_slug != EXPERT_ROUTER_AGENT_SLUG:
            raise ValueError("unknown public Agent")
        reservation_slug = (
            spec.slug if spec is not None else EXPERT_ROUTER_AGENT_SLUG
        )
        reservation_driver: Driver = (
            spec.driver if spec is not None else "local_graph"
        )
        reservation_tool = spec.tool if spec is not None else "ExpertRouter"
        reservation_model = spec.model if spec is not None else None
        reservation_lifecycle = (
            spec.lifecycle if spec is not None else "synchronous"
        )
        reservation_deadline_seconds = (
            spec.deadline_seconds if spec is not None else 3600
        )
        command_hash = execution_command_hash(command)
        durable_command_json = _durable_command_json(durable_command)
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            existing = self._read(connection, owner, execution_id)
            if existing is not None:
                if (
                    existing.fingerprint_version != fingerprint_version
                    or existing.fingerprint != fingerprint
                    or existing.command_hash != command_hash
                    or existing.agent_slug != reservation_slug
                    or existing.driver != reservation_driver
                ):
                    raise ExecutionReservationConflictError(execution_id)
                self._persist_durable_command(
                    connection,
                    owner=owner,
                    execution_id=execution_id,
                    command_json=durable_command_json,
                    created_at=self._clock().isoformat(),
                )
                connection.commit()
                return existing
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("clock must return timezone-aware datetime")
            run_id = (
                self._run_id_factory()
                if self._run_id_factory is not None
                else IdFactory().new_id("run", reservation_slug)
            )
            root_span_id = (
                self._root_span_id_factory()
                if self._root_span_id_factory is not None
                else IdFactory().new_id("span", reservation_slug)
            )
            deadline_at = (
                now + timedelta(seconds=reservation_deadline_seconds)
            ).isoformat()
            connection.execute(
                "INSERT INTO runs ("
                "run_id, user_id, agent, origin, status, created_at, "
                "updated_at, tool_name, model, external_execution_id, "
                "execution_id, execution_fingerprint_version, "
                "execution_fingerprint, execution_command_hash, "
                "execution_driver, execution_deadline_at, "
                "execution_supervisor_revision, execution_root_span_id, "
                "revision) VALUES (?, ?, ?, ?, 'admitted', ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0)",
                (
                    run_id,
                    owner,
                    reservation_slug,
                    (
                        "remote"
                        if reservation_lifecycle == "asynchronous"
                        else "local"
                    ),
                    now.isoformat(),
                    now.isoformat(),
                    reservation_tool,
                    reservation_model,
                    execution_id,
                    execution_id,
                    fingerprint_version,
                    fingerprint,
                    command_hash,
                    reservation_driver,
                    deadline_at,
                    root_span_id,
                ),
            )
            self._persist_durable_command(
                connection,
                owner=owner,
                execution_id=execution_id,
                command_json=durable_command_json,
                created_at=now.isoformat(),
            )
            connection.commit()
        return self.get(owner=owner, execution_id=execution_id)

    def bind_routed_agent(
        self,
        *,
        owner: str,
        execution_id: str,
        command: ExecutionCommand,
    ) -> ExecutionReservationRecord:
        """Replace the private Expert placeholder with its selection.

        The selector remains the sole routing authority.  Rebinding happens
        before any selected Agent business code starts and preserves the
        public execution/run/root-span identities allocated at admission.
        """
        spec = public_agent_spec(command.agent_slug)
        if spec is None:
            raise ValueError("unknown public Agent")
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        command_hash = execution_command_hash(command)
        deadline_at = (
            now + timedelta(seconds=spec.deadline_seconds)
        ).isoformat()
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection, owner, execution_id)
            if current is None:
                raise ExecutionReservationNotFoundError(execution_id)
            if current.agent_slug != EXPERT_ROUTER_AGENT_SLUG:
                if (
                    current.agent_slug == spec.slug
                    and current.driver == spec.driver
                    and current.command_hash == command_hash
                ):
                    connection.commit()
                    return current
                raise ExecutionReservationConflictError(
                    "routing_already_bound"
                )
            if current.status is not ExecutionStatus.ADMITTED:
                raise ExecutionReservationConflictError(
                    "routing_already_started"
                )
            result = connection.execute(
                "UPDATE runs SET agent = ?, origin = ?, "
                "tool_name = ?, model = ?, "
                "execution_command_hash = ?, execution_driver = ?, "
                "execution_deadline_at = ?, revision = revision + 1, "
                "updated_at = ? "
                "WHERE user_id = ? AND execution_id = ? AND agent = ? "
                "AND status = 'admitted' "
                "AND execution_terminal_outcome IS NULL",
                (
                    spec.slug,
                    "remote" if spec.lifecycle == "asynchronous" else "local",
                    spec.tool,
                    spec.model,
                    command_hash,
                    spec.driver,
                    deadline_at,
                    now.isoformat(),
                    owner,
                    execution_id,
                    EXPERT_ROUTER_AGENT_SLUG,
                ),
            )
            if result.rowcount != 1:
                raise ExecutionReservationConflictError(
                    "routing_bind_conflict"
                )
            connection.commit()
        return self.get(owner=owner, execution_id=execution_id)

    def record_context_stage(
        self,
        *,
        owner: str,
        execution_id: str,
        stage: Mapping[str, Any],
    ) -> bool:
        """Persist bounded staged-context metadata beside the execution."""
        raw = dict(stage)
        schema_version = raw.pop("schema_version", None)
        turn_id = raw.pop("turn_id", None)
        context_degraded = raw.pop("context_degraded", None)
        if (
            schema_version != 1
            or not isinstance(turn_id, str)
            or not isinstance(context_degraded, bool)
        ):
            raise ValueError("invalid context stage")
        # ``context_degraded`` belongs to the V1 conversation-context
        # contract.  The execution snapshot intentionally exposes the
        # smaller V2 projection, so validate the V1-only flag above and do
        # not persist it into ``ExecutionContextStageV2``.
        metadata = ContextStageMetadata.model_validate(raw)
        encoded = json.dumps(
            {
                "schema_version": 1,
                "turn_id": turn_id,
                **metadata.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("context stage exceeds size limit")
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            result = connection.execute(
                "UPDATE runs SET execution_context_stage_json = ?, "
                "revision = revision + 1, updated_at = ? WHERE user_id = ? "
                "AND execution_id = ? AND execution_tombstoned_at IS NULL",
                (
                    encoded,
                    self._clock().isoformat(),
                    owner,
                    execution_id,
                ),
            )
            connection.commit()
            return result.rowcount == 1

    @staticmethod
    def _persist_durable_command(
        connection: sqlite3.Connection,
        *,
        owner: str,
        execution_id: str,
        command_json: str | None,
        created_at: str,
    ) -> None:
        """Persist a detached command in the reservation transaction."""
        if command_json is None:
            return
        existing = connection.execute(
            "SELECT command_json FROM execution_commands_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            (owner, execution_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != command_json:
                raise ExecutionReservationConflictError(execution_id)
            return
        connection.execute(
            "INSERT INTO execution_commands_v2 (owner_ref, execution_id, "
            "command_json, state, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (owner, execution_id, command_json, created_at, created_at),
        )

    def get(
        self, *, owner: str, execution_id: str
    ) -> ExecutionReservationRecord:
        with sqlite_transaction(self.db_path) as connection:
            record = self._read(connection, owner, execution_id)
        if record is None:
            raise ExecutionReservationNotFoundError(execution_id)
        return record

    def admitted_assistant_message_id(
        self, *, owner: str, execution_id: str
    ) -> str | None:
        """Read Web's stable assistant identity from the durable admission.

        Expert routing may replace the Agent command used for business
        execution, but it must not replace the message identity committed by
        Web before dispatch.  Only this finite identity is exposed to Runtime;
        the remaining private admission arguments stay inside the queue.
        """
        with sqlite_transaction(self.db_path) as connection:
            row = connection.execute(
                "SELECT command_json FROM execution_commands_v2 "
                "WHERE owner_ref = ? AND execution_id = ?",
                (owner, execution_id),
            ).fetchone()
        if row is None:
            return None
        try:
            command = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return None
        if not isinstance(command, dict):
            return None
        arguments = command.get("arguments")
        if not isinstance(arguments, dict):
            return None
        candidate = arguments.get("__assistant_message_id")
        return candidate if isinstance(candidate, str) else None

    def list_recoverable(
        self, *, limit: int
    ) -> tuple[ExecutionReservationRecord, ...]:
        """List a bounded deterministic cohort lacking terminal settlement."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with sqlite_transaction(self.db_path) as connection:
            rows = connection.execute(
                "SELECT user_id, execution_id FROM runs "
                "WHERE execution_id IS NOT NULL "
                "AND execution_terminal_outcome IS NULL "
                "AND execution_tombstoned_at IS NULL "
                "ORDER BY updated_at, execution_id LIMIT ?",
                (limit,),
            ).fetchall()
            records = tuple(
                self._read(connection, str(owner), str(execution_id))
                for owner, execution_id in rows
            )
        return tuple(record for record in records if record is not None)

    def terminal_authority(
        self, context: ExecutionContext
    ) -> TerminalSettlementAuthority:
        self.require_provider_join_lease(
            owner=context.owner_ref,
            execution_id=context.execution_id,
        )
        record = self.get(
            owner=context.owner_ref,
            execution_id=context.execution_id,
        )
        return TerminalSettlementAuthority(
            owner_ref=record.owner,
            execution_id=record.execution_id,
            expected_revision=record.supervisor_revision,
            actor="runtime",
            issued_at=self._clock(),
        )

    def claim_start(self, *, owner: str, execution_id: str) -> bool:
        """Atomically grant exactly one Runtime caller Driver dispatch."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE runs SET status = 'dispatching', "
                "execution_supervisor_revision = "
                "execution_supervisor_revision + 1, "
                "revision = revision + 1, updated_at = ? WHERE user_id = ? "
                "AND execution_id = ? AND status = 'admitted' "
                "AND execution_terminal_outcome IS NULL",
                (self._clock().isoformat(), owner, execution_id),
            )
            connection.commit()
            return result.rowcount == 1

    def mark_running(self, *, owner: str, execution_id: str) -> bool:
        """Move the dispatch winner to running without reopening terminals."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE runs SET status = 'running', revision = revision + 1, "
                "updated_at = ? WHERE user_id = ? AND execution_id = ? "
                "AND status = 'dispatching' "
                "AND execution_terminal_outcome IS NULL",
                (self._clock().isoformat(), owner, execution_id),
            )
            connection.commit()
            return result.rowcount == 1

    def claim_operation(
        self,
        *,
        owner: str,
        execution_id: str,
        operation_id: str,
        operation: str,
        expected_revision: int,
        command: ExecutionCommand,
    ) -> ExecutionOperationClaim:
        """Claim a revision-checked operation or return its durable replay."""
        if not operation_id or expected_revision < 0:
            raise ValueError("operation identity and revision are required")
        command_hash = execution_command_hash(command)
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT operation, expected_revision, command_hash, state, "
                "outcome_json FROM execution_operations_v2 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND operation_id = ?",
                (owner, execution_id, operation_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != operation
                    or existing[1] != expected_revision
                    or existing[2] != command_hash
                ):
                    raise ExecutionReservationConflictError(
                        "operation_identity_conflict"
                    )
                revision = self._revision(connection, owner, execution_id)
                connection.commit()
                return ExecutionOperationClaim(
                    operation_id=operation_id,
                    operation=operation,
                    expected_revision=expected_revision,
                    command_hash=command_hash,
                    claimed=False,
                    state=existing[3],
                    outcome_json=existing[4],
                    supervisor_revision=revision,
                )
            record = self._read(connection, owner, execution_id)
            if record is None:
                raise ExecutionReservationNotFoundError(execution_id)
            if record.supervisor_revision != expected_revision:
                raise ExecutionReservationConflictError("stale_revision")
            if record.status in _TERMINAL_EXECUTION_STATUSES:
                terminal_row = connection.execute(
                    "SELECT execution_terminal_outcome FROM runs "
                    "WHERE user_id = ? AND execution_id = ?",
                    (owner, execution_id),
                ).fetchone()
                runtime_terminal = (
                    terminal_row is not None and terminal_row[0] is not None
                )
                if operation != "reconcile" or runtime_terminal:
                    raise ExecutionReservationConflictError(
                        "execution_terminal"
                    )
            if (
                operation == "resume"
                and record.status is not ExecutionStatus.WAITING_INPUT
            ):
                raise ExecutionReservationConflictError(
                    "execution_not_waiting_input"
                )
            if operation == "resume":
                active_resume = connection.execute(
                    "SELECT operation_id FROM execution_operations_v2 "
                    "WHERE owner_ref = ? AND execution_id = ? "
                    "AND operation = 'resume' AND state = 'claimed' LIMIT 1",
                    (owner, execution_id),
                ).fetchone()
                if active_resume is not None:
                    raise ExecutionReservationConflictError(
                        "resume_already_claimed"
                    )
            connection.execute(
                "INSERT INTO execution_operations_v2 "
                "(owner_ref, execution_id, operation_id, operation, "
                "expected_revision, command_hash, state, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?, ?)",
                (
                    owner,
                    execution_id,
                    operation_id,
                    operation,
                    expected_revision,
                    command_hash,
                    now,
                    now,
                ),
            )
            fence_clause = (
                " AND execution_provider_join_lease_owner = ? "
                "AND execution_provider_join_lease_expires_at > ?"
                if self._expected_provider_join_lease_token is not None
                else ""
            )
            fence_parameters = (
                (
                    self._expected_provider_join_lease_token,
                    self._clock().isoformat(),
                )
                if self._expected_provider_join_lease_token is not None
                else ()
            )
            result = connection.execute(
                "UPDATE runs SET status = ?, execution_supervisor_revision = "
                "execution_supervisor_revision + 1, revision = revision + 1, "
                "updated_at = ? WHERE user_id = ? AND execution_id = ? "
                "AND execution_supervisor_revision = ? "
                "AND execution_terminal_outcome IS NULL" + fence_clause,
                (
                    record.status.value,
                    now,
                    owner,
                    execution_id,
                    expected_revision,
                    *fence_parameters,
                ),
            )
            if result.rowcount != 1:
                raise ExecutionReservationConflictError("stale_revision")
            connection.commit()
        return ExecutionOperationClaim(
            operation_id=operation_id,
            operation=operation,
            expected_revision=expected_revision,
            command_hash=command_hash,
            claimed=True,
            state="claimed",
            outcome_json=None,
            supervisor_revision=expected_revision + 1,
        )

    def complete_operation(
        self,
        *,
        owner: str,
        execution_id: str,
        operation_id: str,
        outcome_json: str,
    ) -> bool:
        """Persist one normalized operation outcome for idempotent replay."""
        fence_clause, fence_parameters = self._provider_join_fence(
            runs_alias="r"
        )
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE execution_operations_v2 SET state = 'completed', "
                "outcome_json = ?, updated_at = ? WHERE owner_ref = ? "
                "AND execution_id = ? AND operation_id = ? "
                "AND state = 'claimed' AND outcome_json IS NULL "
                "AND EXISTS (SELECT 1 FROM runs r WHERE r.user_id = ? "
                "AND r.execution_id = ?" + fence_clause + ")",
                (
                    outcome_json,
                    self._clock().isoformat(),
                    owner,
                    execution_id,
                    operation_id,
                    owner,
                    execution_id,
                    *fence_parameters,
                ),
            )
            connection.commit()
            return result.rowcount == 1

    def record_observation(
        self,
        *,
        owner: str,
        execution_id: str,
        status: ExecutionStatus,
        tracking_health: str,
        cancellation_state: str,
        next_attempt_at: str | None,
    ) -> bool:
        """Persist a non-terminal Runtime observation without polling."""
        if status in _TERMINAL_EXECUTION_STATUSES:
            raise ValueError("terminal observations use settle_terminal")
        fence_clause, fence_parameters = self._provider_join_fence()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE runs SET status = ?, execution_tracking_health = ?, "
                "execution_cancellation_state = ?, "
                "execution_next_attempt_at = ?, "
                "revision = revision + 1, updated_at = ? WHERE user_id = ? "
                "AND execution_id = ? AND execution_terminal_outcome IS NULL"
                + fence_clause,
                (
                    status.value,
                    tracking_health,
                    cancellation_state,
                    next_attempt_at,
                    self._clock().isoformat(),
                    owner,
                    execution_id,
                    *fence_parameters,
                ),
            )
            connection.commit()
            return result.rowcount == 1

    def settle_terminal(
        self,
        authority: TerminalSettlementAuthority,
        outcome: DriverOutcome,
    ) -> bool:
        """Commit one authoritative terminal decision and its facts atomically.

        The reservation CAS is the sole terminal authority.  Root-span state,
        terminal journal facts, and the rebuildable projection share its
        ``BEGIN IMMEDIATE`` transaction, so a stale contender publishes
        nothing.  Exact replays succeed only when the durable reservation and
        projection already agree on the same terminal outcome.
        """
        if not outcome.terminal:
            raise ValueError("terminal outcome required")
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, execution_terminal_outcome, "
                "execution_cancellation_state, execution_root_span_id, "
                "agent, execution_supervisor_revision "
                "FROM runs WHERE user_id = ? AND execution_id = ? "
                "AND execution_tombstoned_at IS NULL",
                (authority.owner_ref, authority.execution_id),
            ).fetchone()
            if row is None:
                raise ExecutionReservationNotFoundError(authority.execution_id)
            projection = self._journal._load_or_rebuild_projection(
                connection,
                authority.owner_ref,
                authority.execution_id,
            )
            existing_terminal = row[1]
            if existing_terminal is not None:
                connection.commit()
                return (
                    str(existing_terminal) == outcome.status.value
                    and projection.terminal is not None
                    and projection.terminal.status == outcome.status.value
                )
            if int(row[5]) != authority.expected_revision:
                connection.commit()
                return False
            if (
                projection.terminal is not None
                and projection.terminal.status != outcome.status.value
            ):
                connection.commit()
                return False

            root_span_id = str(row[3])
            root_span = connection.execute(
                "SELECT status FROM execution_spans WHERE owner_ref = ? "
                "AND execution_id = ? AND span_id = ?",
                (
                    authority.owner_ref,
                    authority.execution_id,
                    root_span_id,
                ),
            ).fetchone()
            desired_span_status = outcome.status.value
            if (
                root_span is not None
                and str(root_span[0])
                in {status.value for status in _TERMINAL_EXECUTION_STATUSES}
                and str(root_span[0]) != desired_span_status
            ):
                connection.commit()
                return False

            cancellation_state: str | None = outcome.cancellation_outcome
            if cancellation_state is None:
                cancellation_state = (
                    "confirmed"
                    if outcome.status is ExecutionStatus.CANCELLED
                    else str(row[2])
                )
            result_json = _public_result_json(outcome)
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("clock must return timezone-aware datetime")
            fence_clause, fence_parameters = self._provider_join_fence(now=now)
            result = connection.execute(
                "UPDATE runs SET status = ?, execution_terminal_outcome = ?, "
                "execution_cancellation_state = ?, "
                "result_json = COALESCE(result_json, ?), "
                "expires_at = ?, "
                "execution_supervisor_revision = "
                "execution_supervisor_revision + 1, "
                "revision = revision + 1, updated_at = ? "
                "WHERE user_id = ? AND execution_id = ? "
                "AND execution_supervisor_revision = ? "
                "AND execution_terminal_outcome IS NULL" + fence_clause,
                (
                    outcome.status.value,
                    outcome.status.value,
                    cancellation_state,
                    result_json,
                    _terminal_expiry(outcome.status, now),
                    now.isoformat(),
                    authority.owner_ref,
                    authority.execution_id,
                    authority.expected_revision,
                    *fence_parameters,
                ),
            )
            if result.rowcount != 1:
                connection.commit()
                return False

            if (
                root_span is not None
                and str(root_span[0]) != desired_span_status
            ):
                connection.execute(
                    "UPDATE execution_spans SET status = ?, "
                    "last_activity_at = ?, ended_at = ?, "
                    "revision = revision + 1 WHERE owner_ref = ? "
                    "AND execution_id = ? AND span_id = ?",
                    (
                        desired_span_status,
                        now.isoformat(),
                        now.isoformat(),
                        authority.owner_ref,
                        authority.execution_id,
                        root_span_id,
                    ),
                )

            # A legacy append-before-settle record may already carry the same
            # terminal projection.  Settle its reservation without duplicating
            # that fact; new writes always append inside this transaction.
            if projection.terminal is None:
                span_intent, execution_intent = _terminal_event_intents(
                    execution_id=authority.execution_id,
                    root_span_id=root_span_id,
                    agent_slug=str(row[4]),
                    actor=authority.actor,
                    outcome=outcome,
                )
                if root_span is not None:
                    self._journal._append_locked(
                        connection,
                        execution_id=authority.execution_id,
                        owner=authority.owner_ref,
                        intent=span_intent,
                    )
                self._journal._append_locked(
                    connection,
                    execution_id=authority.execution_id,
                    owner=authority.owner_ref,
                    intent=execution_intent,
                )
            connection.commit()
            return True

    def provider_join_lease_valid(
        self,
        *,
        owner: str,
        execution_id: str,
    ) -> bool:
        """Return whether this repository's optional fence is still live."""
        token = self._expected_provider_join_lease_token
        if token is None:
            return True
        with sqlite_transaction(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
                "AND execution_provider_join_lease_owner = ? "
                "AND execution_provider_join_lease_expires_at > ?",
                (owner, execution_id, token, self._clock().isoformat()),
            ).fetchone()
        return row is not None

    def require_provider_join_lease(
        self,
        *,
        owner: str,
        execution_id: str,
    ) -> None:
        """Reject stale provider-join work before it publishes an outcome."""
        if not self.provider_join_lease_valid(
            owner=owner, execution_id=execution_id
        ):
            raise ExecutionReservationConflictError("provider_join_lease_lost")

    def _provider_join_fence(
        self,
        *,
        runs_alias: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        token = self._expected_provider_join_lease_token
        if token is None:
            return "", ()
        prefix = f"{runs_alias}." if runs_alias else ""
        timestamp = (now or self._clock()).isoformat()
        return (
            f" AND {prefix}execution_provider_join_lease_owner = ? "
            f"AND {prefix}execution_provider_join_lease_expires_at > ?",
            (token, timestamp),
        )

    @staticmethod
    def _read(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> ExecutionReservationRecord | None:
        row = connection.execute(
            "SELECT user_id, execution_id, run_id, "
            "execution_fingerprint_version, execution_fingerprint, "
            "execution_command_hash, agent, execution_driver, "
            "execution_root_span_id, status, execution_deadline_at, "
            "execution_supervisor_revision, execution_next_attempt_at, "
            "execution_tracking_health, execution_cancellation_state, "
            "execution_context_stage_json "
            "FROM runs WHERE user_id = ? "
            "AND execution_id = ? AND execution_tombstoned_at IS NULL",
            (owner, execution_id),
        ).fetchone()
        if row is None:
            return None
        return ExecutionReservationRecord(
            owner=row[0],
            execution_id=row[1],
            run_id=row[2],
            fingerprint_version=row[3],
            fingerprint=row[4],
            command_hash=row[5],
            agent_slug=row[6],
            driver=cast(Driver, row[7]),
            root_span_id=row[8],
            status=ExecutionStatus(row[9]),
            deadline_at=row[10],
            supervisor_revision=row[11],
            next_attempt_at=row[12],
            tracking_health=row[13],
            cancellation_state=row[14],
            context_stage_json=row[15],
        )

    @staticmethod
    def _revision(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> int:
        row = connection.execute(
            "SELECT execution_supervisor_revision FROM runs "
            "WHERE user_id = ? AND execution_id = ? "
            "AND execution_tombstoned_at IS NULL",
            (owner, execution_id),
        ).fetchone()
        if row is None:
            raise ExecutionReservationNotFoundError(execution_id)
        return int(row[0])


def execution_command_hash(command: ExecutionCommand) -> str:
    """Return the canonical durable hash used by reservation fences."""
    try:
        encoded = json.dumps(
            {
                "agent_slug": command.agent_slug,
                "arguments": command.arguments,
                "action_id": command.action_id,
                "expected_revision": command.expected_revision,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ValueError("Agent command is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def routed_binding_matches_command(
    durable_command: Mapping[str, object],
    *,
    owner: str,
    execution_id: str,
    reservation_agent: str,
    reservation_command_hash: str,
    fingerprint_version: object,
    fingerprint: object,
) -> bool:
    """Validate the persisted selected binding below one router command."""
    arguments = durable_command.get("arguments")
    if (
        durable_command.get("owner_ref") != owner
        or durable_command.get("execution_id") != execution_id
        or durable_command.get("agent") != EXPERT_ROUTER_AGENT_SLUG
        or not isinstance(arguments, dict)
        or durable_command.get("fingerprint_version") != fingerprint_version
        or durable_command.get("fingerprint") != fingerprint
        or reservation_agent == EXPERT_ROUTER_AGENT_SLUG
        or len(reservation_command_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in reservation_command_hash
        )
    ):
        return False
    spec = public_agent_spec(reservation_agent)
    if spec is None or spec.lifecycle != "asynchronous":
        return False
    allowed_tools = arguments.get("__allowed_tools")
    if not isinstance(allowed_tools, list) or spec.tool not in allowed_tools:
        return False
    forced_tool = arguments.get("__forced_tool")
    if isinstance(forced_tool, str) and forced_tool != spec.tool:
        return False
    conversation = arguments.get("__conversation")
    if not isinstance(conversation, dict) or conversation.get("mode") != (
        "expert"
    ):
        return False
    try:
        router_hash = execution_command_hash(
            ExecutionCommand(
                agent_slug=EXPERT_ROUTER_AGENT_SLUG,
                arguments=arguments,
            )
        )
    except (TypeError, ValueError):
        return False
    return reservation_command_hash != router_hash


def _terminal_event_intents(
    *,
    execution_id: str,
    root_span_id: str,
    agent_slug: str,
    actor: str,
    outcome: DriverOutcome,
) -> tuple[ExecutionEventIntentV2, ExecutionEventIntentV2]:
    """Build the bounded public facts owned by terminal settlement."""
    if outcome.status is ExecutionStatus.SUCCEEDED:
        execution_type = ExecutionEventType.EXECUTION_SUCCEEDED
        span_type = ExecutionEventType.SPAN_SUCCEEDED
        payload: dict[str, object] = {}
    elif outcome.status is ExecutionStatus.PARTIAL:
        execution_type = ExecutionEventType.EXECUTION_PARTIAL
        span_type = ExecutionEventType.SPAN_PARTIAL
        payload = {"code": "partial_result", "retryable": False}
    elif outcome.status is ExecutionStatus.FAILED:
        assert outcome.failure is not None
        execution_type = ExecutionEventType.EXECUTION_FAILED
        span_type = ExecutionEventType.SPAN_FAILED
        payload = {
            "code": outcome.failure.code,
            "retryable": outcome.failure.retryable,
        }
    elif outcome.status is ExecutionStatus.CANCELLED:
        execution_type = ExecutionEventType.EXECUTION_CANCELLED
        span_type = ExecutionEventType.SPAN_CANCELLED
        payload = {"outcome": "best_effort"}
    elif outcome.status is ExecutionStatus.TIMED_OUT:
        execution_type = ExecutionEventType.EXECUTION_TIMED_OUT
        span_type = ExecutionEventType.SPAN_TIMED_OUT
        payload = {"code": "execution_timed_out", "retryable": False}
    else:  # pragma: no cover - guarded by DriverOutcome.terminal
        raise ValueError("terminal outcome required")

    event_status = EventStatus(outcome.status.value)
    readable_status = outcome.status.value.replace("_", " ")
    span_payload = (
        {"phase": agent_slug}
        if span_type
        in {
            ExecutionEventType.SPAN_SUCCEEDED,
            ExecutionEventType.SPAN_CANCELLED,
        }
        else payload
    )
    span_intent = parse_execution_event_intent_v2(
        {
            "type": span_type.value,
            "status": event_status.value,
            "source": actor,
            "span_id": root_span_id,
            "attempt": 1,
            "summary": {
                "key": f"agent.{agent_slug}.{outcome.status.value}",
                "text": f"Agent {readable_status}",
            },
            "public_payload": span_payload,
            "idempotency_key": f"terminal:{execution_id}:root-span",
        }
    )
    execution_intent = parse_execution_event_intent_v2(
        {
            "type": execution_type.value,
            "status": event_status.value,
            "source": actor,
            "span_id": root_span_id,
            "attempt": 1,
            "summary": {
                "key": f"execution.{outcome.status.value}",
                "text": f"Execution {readable_status}",
            },
            "public_payload": payload,
            "idempotency_key": f"terminal:{execution_id}:execution",
        }
    )
    return span_intent, execution_intent


def _public_result_json(outcome: DriverOutcome) -> str | None:
    """Serialize the public result, never private transport data."""
    if outcome.result is None:
        return None
    result = outcome.result
    public_result: dict[str, Any] = {
        "answer": result.answer,
        "follow_up_questions": list(result.follow_up_questions),
        "references": [dict(reference) for reference in result.references],
        "artifacts": [
            {
                "role": artifact.role,
                "target_kind": str(artifact.target_kind),
                "target_id": artifact.target_id,
                "name": artifact.name,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
            }
            for artifact in result.artifacts
        ],
        "metadata": dict(result.public_metadata or {}),
    }
    if result.tabular is not None:
        public_result["tabular"] = result.tabular.to_public_dict()
    return json.dumps(
        public_result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _terminal_expiry(status: ExecutionStatus, now: datetime) -> str:
    """Apply the existing run-retention policy to V2 terminal settlement."""
    config = ApiConfig()
    delta = (
        timedelta(hours=config.API_RUN_TTL_OK_HOURS)
        if status is ExecutionStatus.SUCCEEDED
        else timedelta(days=config.API_RUN_TTL_FAIL_DAYS)
    )
    return (now + delta).isoformat()


def _durable_command_json(command: Mapping[str, Any] | None) -> str | None:
    if command is None:
        return None
    try:
        encoded = json.dumps(
            command,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, UnicodeEncodeError) as exc:
        raise ValueError("durable command is not canonical JSON") from exc
    if len(encoded.encode("utf-8")) > 262_144:
        raise ValueError("durable command exceeds size limit")
    return encoded


_TERMINAL_EXECUTION_STATUSES = {
    ExecutionStatus.SUCCEEDED,
    ExecutionStatus.PARTIAL,
    ExecutionStatus.FAILED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.TIMED_OUT,
}


__all__ = [
    "EXPERT_ROUTER_AGENT_SLUG",
    "ExecutionOperationClaim",
    "ExecutionReservationConflictError",
    "ExecutionReservationNotFoundError",
    "ExecutionReservationRecord",
    "SQLiteExecutionReservationRepository",
    "execution_command_hash",
    "routed_binding_matches_command",
]
