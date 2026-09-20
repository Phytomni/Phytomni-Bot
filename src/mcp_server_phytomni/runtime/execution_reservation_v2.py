# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Idempotent owner/execution reservation before Agent business execution."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any, Unpack

from ..public_agent_catalog import public_agent_spec
from ..storage.path_policy import IdFactory
from .conversation_context.models import ContextStageMetadata
from .execution_journal_store_v2 import SQLiteExecutionJournal
from .execution_journal_v2 import ExecutionStatus
from .execution_reservation_support_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    OBSERVATION_SIGNATURE,
    OPERATION_CLAIM_SIGNATURE,
    RESERVE_SIGNATURE,
    TERMINAL_EXECUTION_STATUSES,
    ExecutionOperationClaim,
    ExecutionReservationRecord,
    ObservationKwargs,
    OperationClaimKwargs,
    OperationClaimRequest,
    ReservationKwargs,
    ReservationPlan,
    TerminalPlan,
    TerminalSnapshot,
    append_event_locked,
    execution_command_hash,
    observation_request,
    operation_claim_request,
    reservation_plan,
    reservation_record,
    reservation_replay_matches,
    reservation_request,
    routed_binding_matches_command,
    terminal_event_intents,
    terminal_plan,
    terminal_replay_result,
    terminal_snapshot,
)
from .execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    TerminalSettlementAuthority,
)
from .execution_store_support_v2 import validate_provider_join_lease_token
from .sqlite import sqlite_transaction


class ExecutionReservationConflictError(RuntimeError):
    """The owner/execution key was already bound to different input."""


class ExecutionReservationNotFoundError(LookupError):
    """The owner-scoped execution binding does not exist."""


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
        self.db_path = db_path
        self._run_id_factory = run_id_factory
        self._root_span_id_factory = root_span_id_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expected_provider_join_lease_token = (
            validate_provider_join_lease_token(
                expected_provider_join_lease_token
            )
        )
        registry_module = import_module(".run_registry", __package__)
        registry_module.RunRegistry(db_path)
        self._journal = SQLiteExecutionJournal(
            db_path,
            clock=lambda: self._clock().isoformat(),
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        )

    def reserve(
        self,
        **kwargs: Unpack[ReservationKwargs],
    ) -> ExecutionReservationRecord:
        """Reserve once; exact retries return the original binding."""
        request = reservation_request(self, kwargs)
        plan = reservation_plan(request)
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            existing = self._read(
                connection,
                request.owner,
                request.execution_id,
            )
            if existing is not None:
                if not reservation_replay_matches(existing, plan):
                    raise ExecutionReservationConflictError(
                        request.execution_id
                    )
                self._persist_durable_command(
                    connection,
                    owner=request.owner,
                    execution_id=request.execution_id,
                    command_json=plan.durable_command_json,
                    created_at=self._clock().isoformat(),
                )
                connection.commit()
                return existing
            self._insert_reservation(connection, plan)
            connection.commit()
        return self.get(
            owner=request.owner,
            execution_id=request.execution_id,
        )

    def _insert_reservation(
        self,
        connection: sqlite3.Connection,
        plan: ReservationPlan,
    ) -> None:
        request = plan.request
        profile = plan.profile
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        run_id = (
            self._run_id_factory()
            if self._run_id_factory is not None
            else IdFactory().new_id("run", profile.slug)
        )
        root_span_id = (
            self._root_span_id_factory()
            if self._root_span_id_factory is not None
            else IdFactory().new_id("span", profile.slug)
        )
        deadline_at = (
            now + timedelta(seconds=profile.deadline_seconds)
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
                request.owner,
                profile.slug,
                "remote" if profile.lifecycle == "asynchronous" else "local",
                now.isoformat(),
                now.isoformat(),
                profile.tool,
                profile.model,
                request.execution_id,
                request.execution_id,
                request.fingerprint_version,
                request.fingerprint,
                plan.command_hash,
                profile.driver,
                deadline_at,
                root_span_id,
            ),
        )
        self._persist_durable_command(
            connection,
            owner=request.owner,
            execution_id=request.execution_id,
            command_json=plan.durable_command_json,
            created_at=now.isoformat(),
        )

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
        """Read one live owner-scoped execution reservation."""
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
        """Issue terminal authority from the current supervisor revision."""
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
        **kwargs: Unpack[OperationClaimKwargs],
    ) -> ExecutionOperationClaim:
        """Claim a revision-checked operation or return its durable replay."""
        request = operation_claim_request(self, kwargs)
        command_hash = execution_command_hash(request.command)
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT operation, expected_revision, command_hash, state, "
                "outcome_json FROM execution_operations_v2 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND operation_id = ?",
                (
                    request.owner,
                    request.execution_id,
                    request.operation_id,
                ),
            ).fetchone()
            if existing is not None:
                replay = self._operation_replay(
                    connection,
                    request,
                    command_hash,
                    existing,
                )
                connection.commit()
                return replay
            record = self._read(
                connection,
                request.owner,
                request.execution_id,
            )
            if record is None:
                raise ExecutionReservationNotFoundError(request.execution_id)
            self._validate_operation_claim(connection, request, record)
            self._insert_operation_claim(
                connection,
                request,
                command_hash,
                now,
            )
            self._advance_operation_revision(
                connection,
                request,
                record.status,
                now,
            )
            connection.commit()
        return ExecutionOperationClaim(
            operation_id=request.operation_id,
            operation=request.operation,
            expected_revision=request.expected_revision,
            command_hash=command_hash,
            claimed=True,
            state="claimed",
            outcome_json=None,
            supervisor_revision=request.expected_revision + 1,
        )

    def _operation_replay(
        self,
        connection: sqlite3.Connection,
        request: OperationClaimRequest,
        command_hash: str,
        existing: sqlite3.Row | tuple[Any, ...],
    ) -> ExecutionOperationClaim:
        if (
            existing[0] != request.operation
            or existing[1] != request.expected_revision
            or existing[2] != command_hash
        ):
            raise ExecutionReservationConflictError(
                "operation_identity_conflict"
            )
        revision = self._revision(
            connection,
            request.owner,
            request.execution_id,
        )
        return ExecutionOperationClaim(
            operation_id=request.operation_id,
            operation=request.operation,
            expected_revision=request.expected_revision,
            command_hash=command_hash,
            claimed=False,
            state=str(existing[3]),
            outcome_json=None if existing[4] is None else str(existing[4]),
            supervisor_revision=revision,
        )

    @staticmethod
    def _validate_operation_claim(
        connection: sqlite3.Connection,
        request: OperationClaimRequest,
        record: ExecutionReservationRecord,
    ) -> None:
        if record.supervisor_revision != request.expected_revision:
            raise ExecutionReservationConflictError("stale_revision")
        if record.status in TERMINAL_EXECUTION_STATUSES:
            terminal_row = connection.execute(
                "SELECT execution_terminal_outcome FROM runs "
                "WHERE user_id = ? AND execution_id = ?",
                (request.owner, request.execution_id),
            ).fetchone()
            runtime_terminal = (
                terminal_row is not None and terminal_row[0] is not None
            )
            if request.operation != "reconcile" or runtime_terminal:
                raise ExecutionReservationConflictError("execution_terminal")
        if (
            request.operation == "resume"
            and record.status is not ExecutionStatus.WAITING_INPUT
        ):
            raise ExecutionReservationConflictError(
                "execution_not_waiting_input"
            )
        if request.operation == "resume":
            active_resume = connection.execute(
                "SELECT operation_id FROM execution_operations_v2 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND operation = 'resume' AND state = 'claimed' LIMIT 1",
                (request.owner, request.execution_id),
            ).fetchone()
            if active_resume is not None:
                raise ExecutionReservationConflictError(
                    "resume_already_claimed"
                )

    @staticmethod
    def _insert_operation_claim(
        connection: sqlite3.Connection,
        request: OperationClaimRequest,
        command_hash: str,
        now: str,
    ) -> None:
        connection.execute(
            "INSERT INTO execution_operations_v2 "
            "(owner_ref, execution_id, operation_id, operation, "
            "expected_revision, command_hash, state, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?, ?)",
            (
                request.owner,
                request.execution_id,
                request.operation_id,
                request.operation,
                request.expected_revision,
                command_hash,
                now,
                now,
            ),
        )

    def _advance_operation_revision(
        self,
        connection: sqlite3.Connection,
        request: OperationClaimRequest,
        status: ExecutionStatus,
        now: str,
    ) -> None:
        fence_clause, fence_parameters = self._provider_join_fence()
        result = connection.execute(
            "UPDATE runs SET status = ?, execution_supervisor_revision = "
            "execution_supervisor_revision + 1, revision = revision + 1, "
            "updated_at = ? WHERE user_id = ? AND execution_id = ? "
            "AND execution_supervisor_revision = ? "
            "AND execution_terminal_outcome IS NULL" + fence_clause,
            (
                status.value,
                now,
                request.owner,
                request.execution_id,
                request.expected_revision,
                *fence_parameters,
            ),
        )
        if result.rowcount != 1:
            raise ExecutionReservationConflictError("stale_revision")

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
        **kwargs: Unpack[ObservationKwargs],
    ) -> bool:
        """Persist a non-terminal Runtime observation without polling."""
        request = observation_request(self, kwargs)
        if request.status in TERMINAL_EXECUTION_STATUSES:
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
                    request.status.value,
                    request.tracking_health,
                    request.cancellation_state,
                    request.next_attempt_at,
                    self._clock().isoformat(),
                    request.owner,
                    request.execution_id,
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
            snapshot = terminal_snapshot(
                self._journal,
                connection,
                authority,
                row,
            )
            replay_result = terminal_replay_result(
                snapshot,
                authority,
                outcome,
            )
            if replay_result is not None:
                connection.commit()
                return replay_result
            plan = terminal_plan(snapshot, outcome, self._clock)
            if not self._settle_terminal_reservation(
                connection,
                authority,
                plan,
            ):
                connection.commit()
                return False
            self._settle_root_span(connection, authority, snapshot, plan)
            self._append_terminal_facts(
                connection,
                authority,
                snapshot,
                outcome,
            )
            connection.commit()
            return True

    def _settle_terminal_reservation(
        self,
        connection: sqlite3.Connection,
        authority: TerminalSettlementAuthority,
        plan: TerminalPlan,
    ) -> bool:
        fence_clause, fence_parameters = self._provider_join_fence(
            now=plan.now
        )
        result = connection.execute(
            "UPDATE runs SET status = ?, execution_terminal_outcome = ?, "
            "execution_cancellation_state = ?, "
            "result_json = COALESCE(result_json, ?), expires_at = ?, "
            "execution_supervisor_revision = "
            "execution_supervisor_revision + 1, "
            "revision = revision + 1, updated_at = ? "
            "WHERE user_id = ? AND execution_id = ? "
            "AND execution_supervisor_revision = ? "
            "AND execution_terminal_outcome IS NULL" + fence_clause,
            (
                plan.status.value,
                plan.status.value,
                plan.cancellation_state,
                plan.result_json,
                plan.expires_at,
                plan.now.isoformat(),
                authority.owner_ref,
                authority.execution_id,
                authority.expected_revision,
                *fence_parameters,
            ),
        )
        return result.rowcount == 1

    @staticmethod
    def _settle_root_span(
        connection: sqlite3.Connection,
        authority: TerminalSettlementAuthority,
        snapshot: TerminalSnapshot,
        plan: TerminalPlan,
    ) -> None:
        if (
            snapshot.root_span_status is None
            or snapshot.root_span_status == plan.status.value
        ):
            return
        connection.execute(
            "UPDATE execution_spans SET status = ?, "
            "last_activity_at = ?, ended_at = ?, "
            "revision = revision + 1 WHERE owner_ref = ? "
            "AND execution_id = ? AND span_id = ?",
            (
                plan.status.value,
                plan.now.isoformat(),
                plan.now.isoformat(),
                authority.owner_ref,
                authority.execution_id,
                snapshot.reservation.root_span_id,
            ),
        )

    def _append_terminal_facts(
        self,
        connection: sqlite3.Connection,
        authority: TerminalSettlementAuthority,
        snapshot: TerminalSnapshot,
        outcome: DriverOutcome,
    ) -> None:
        # A legacy append-before-settle record may already carry the same
        # terminal projection.  Settle its reservation without duplicating
        # that fact; new writes always append inside this transaction.
        if snapshot.projection.terminal is not None:
            return
        span_intent, execution_intent = terminal_event_intents(
            execution_id=authority.execution_id,
            root_span_id=snapshot.reservation.root_span_id,
            agent_slug=snapshot.reservation.agent_slug,
            actor=authority.actor,
            outcome=outcome,
        )
        if snapshot.root_span_status is not None:
            append_event_locked(
                self._journal,
                connection,
                authority.execution_id,
                authority.owner_ref,
                span_intent,
            )
        append_event_locked(
            self._journal,
            connection,
            authority.execution_id,
            authority.owner_ref,
            execution_intent,
        )

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
        return reservation_record(row)

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


for _public_value in (
    ExecutionOperationClaim,
    ExecutionReservationRecord,
    execution_command_hash,
    routed_binding_matches_command,
):
    setattr(_public_value, "__module__", __name__)
del _public_value

for _method, _signature in (
    (SQLiteExecutionReservationRepository.reserve, RESERVE_SIGNATURE),
    (
        SQLiteExecutionReservationRepository.claim_operation,
        OPERATION_CLAIM_SIGNATURE,
    ),
    (
        SQLiteExecutionReservationRepository.record_observation,
        OBSERVATION_SIGNATURE,
    ),
):
    setattr(_method, "__signature__", _signature)
del _method, _signature


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
