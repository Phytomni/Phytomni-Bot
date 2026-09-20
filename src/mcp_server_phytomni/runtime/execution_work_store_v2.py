# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite repositories for V2 causal spans and logical work units."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Unpack

from .execution_journal_schema import EXECUTION_V2_WORK_UNIT_COLUMNS
from .execution_journal_v2 import SpanStatus, WorkUnitStatus
from .execution_provider_join_store_v2 import ProviderJoinLeaseRepositoryMixin
from .execution_store_support_v2 import execution_is_live
from .execution_work_models_v2 import (
    CancellationState,
    ExecutionWorkConflictError,
    ExecutionWorkInvariantError,
    ExecutionWorkNotFoundError,
    Identifier,
    JoinPolicy,
    MissingOrConflictFields,
    ProviderBindingFields,
    ProviderTraceHealth,
    ProviderTraceStateFields,
    SpanRecord,
    SpanSpec,
    WorkLeaseClaimFields,
    WorkRetryFields,
    WorkUnitRecord,
    WorkUnitSpec,
    WorkUnitUpdateFields,
)
from .execution_work_status_v2 import TERMINAL_WORK_UNIT_VALUES
from .sqlite import sqlite_transaction

_PROVIDER_TRACE_UPDATE_COLUMNS = frozenset(
    column
    for column, _column_type in EXECUTION_V2_WORK_UNIT_COLUMNS
    if column.startswith("provider_trace_")
)

__all__ = [
    "CancellationState",
    "ExecutionWorkConflictError",
    "ExecutionWorkInvariantError",
    "ExecutionWorkNotFoundError",
    "Identifier",
    "JoinPolicy",
    "ProviderBindingFields",
    "ProviderTraceHealth",
    "SQLiteExecutionWorkRepository",
    "SpanRecord",
    "SpanSpec",
    "WorkUnitRecord",
    "WorkUnitSpec",
]


class SQLiteExecutionWorkRepository(ProviderJoinLeaseRepositoryMixin):
    """CAS-protected span/work-unit state over the shared registry DB."""

    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = db_path
        self._clock = clock or (lambda: datetime.now(UTC))
        registry_module = import_module(".run_registry", __package__)
        registry_module.RunRegistry(db_path)

    def create_span(self, spec: SpanSpec) -> SpanRecord:
        """Create one stable span or return the identical existing row."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, spec.owner, spec.execution_id)
            existing = self._read_span(
                connection,
                spec.owner,
                spec.execution_id,
                spec.span_id,
            )
            if existing is not None:
                if existing.to_spec() != spec:
                    raise ExecutionWorkConflictError(spec.span_id)
                connection.commit()
                return existing
            if (
                spec.parent_span_id is not None
                and self._read_span(
                    connection,
                    spec.owner,
                    spec.execution_id,
                    spec.parent_span_id,
                )
                is None
            ):
                raise ExecutionWorkInvariantError("parent_span_not_found")
            connection.execute(
                "INSERT INTO execution_spans ("
                "owner_ref, execution_id, span_id, parent_span_id, "
                "work_unit_id, kind, label_key, status, attempt, join_policy, "
                "revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    spec.owner,
                    spec.execution_id,
                    spec.span_id,
                    spec.parent_span_id,
                    spec.work_unit_id,
                    spec.kind,
                    spec.label_key,
                    SpanStatus.PENDING.value,
                    spec.attempt,
                    spec.join_policy,
                ),
            )
            connection.commit()
        return self.get_span(spec.execution_id, spec.span_id, owner=spec.owner)

    def get_span(
        self,
        execution_id: str,
        span_id: str,
        *,
        owner: str,
    ) -> SpanRecord:
        """Read one owner-scoped span or raise a stable not-found error."""
        with sqlite_transaction(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            record = self._read_span(connection, owner, execution_id, span_id)
        if record is None:
            raise ExecutionWorkNotFoundError(span_id)
        return record

    def find_span_by_work_unit_id(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
    ) -> SpanRecord | None:
        """Resolve the latest attempt span owned by one logical work unit."""
        with sqlite_transaction(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            row = connection.execute(
                "SELECT span_id FROM execution_spans WHERE owner_ref = ? "
                "AND execution_id = ? AND work_unit_id = ? "
                "ORDER BY attempt DESC, revision DESC LIMIT 1",
                (owner, execution_id, work_unit_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_span(execution_id, row[0], owner=owner)

    def update_span_status(
        self,
        execution_id: str,
        span_id: str,
        *,
        owner: str,
        status: SpanStatus | str,
        expected_revision: int,
    ) -> SpanRecord:
        """CAS-update span status and its lifecycle timestamps."""
        status = SpanStatus(status)
        now = self._clock().isoformat()
        terminal = status in {
            SpanStatus.SUCCEEDED,
            SpanStatus.PARTIAL,
            SpanStatus.FAILED,
            SpanStatus.CANCELLED,
            SpanStatus.TIMED_OUT,
            SpanStatus.SKIPPED,
        }
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            result = connection.execute(
                "UPDATE execution_spans SET status = ?, "
                "started_at = CASE WHEN ? = 'running' "
                "THEN COALESCE(started_at, ?) ELSE started_at END, "
                "last_activity_at = ?, ended_at = CASE WHEN ? THEN ? "
                "ELSE ended_at END, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? AND span_id = ? "
                "AND revision = ?",
                (
                    status.value,
                    status.value,
                    now,
                    now,
                    terminal,
                    now,
                    owner,
                    execution_id,
                    span_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                self._raise_missing_or_conflict(
                    connection,
                    table="execution_spans",
                    id_column="span_id",
                    owner=owner,
                    execution_id=execution_id,
                    identity=span_id,
                )
            connection.commit()
        return self.get_span(execution_id, span_id, owner=owner)

    def create_work_unit(self, spec: WorkUnitSpec) -> WorkUnitRecord:
        """Create an idempotent logical unit under an existing parent span."""
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, spec.owner, spec.execution_id)
            existing = self._read_work_unit(
                connection,
                spec.owner,
                spec.execution_id,
                spec.work_unit_id,
            )
            if existing is not None:
                if _work_spec(existing) != spec:
                    raise ExecutionWorkConflictError(spec.work_unit_id)
                connection.commit()
                return existing
            if (
                self._read_span(
                    connection,
                    spec.owner,
                    spec.execution_id,
                    spec.parent_span_id,
                )
                is None
            ):
                raise ExecutionWorkInvariantError("parent_span_not_found")
            connection.execute(
                "INSERT INTO execution_work_units ("
                "owner_ref, execution_id, work_unit_id, parent_span_id, "
                "operation_key, driver, status, join_policy, attempt, "
                "max_attempts, cancellation_state, deadline_at, created_at, "
                "updated_at, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, "
                "'none', ?, ?, ?, 0)",
                (
                    spec.owner,
                    spec.execution_id,
                    spec.work_unit_id,
                    spec.parent_span_id,
                    spec.operation_key,
                    spec.driver,
                    WorkUnitStatus.PENDING.value,
                    spec.join_policy,
                    spec.max_attempts,
                    spec.deadline_at,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get_work_unit(
            spec.execution_id,
            spec.work_unit_id,
            owner=spec.owner,
        )

    def get_work_unit(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
    ) -> WorkUnitRecord:
        """Read one owner-scoped work unit or raise a stable error."""
        with sqlite_transaction(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            record = self._read_work_unit(
                connection, owner, execution_id, work_unit_id
            )
        if record is None:
            raise ExecutionWorkNotFoundError(work_unit_id)
        return record

    def find_work_unit_by_provider_task_id(
        self,
        execution_id: str,
        provider_task_id: str,
        *,
        owner: str,
        provider_kind: str,
    ) -> WorkUnitRecord | None:
        """Resolve a private provider identity within one owned execution."""
        with sqlite_transaction(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            row = connection.execute(
                "SELECT work_unit_id FROM execution_work_units "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND provider_kind = ? AND provider_task_id = ? LIMIT 1",
                (owner, execution_id, provider_kind, provider_task_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_work_unit(execution_id, row[0], owner=owner)

    def list_due_work_units(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[WorkUnitRecord, ...]:
        """List globally due non-terminal work in deterministic order."""
        return self._list_due_work_units(
            now=now,
            limit=limit,
            provider_bound_only=False,
        )

    def list_due_provider_work_units(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[WorkUnitRecord, ...]:
        """List only work that the provider reconciler can safely recover."""
        return self._list_due_work_units(
            now=now,
            limit=limit,
            provider_bound_only=True,
        )

    def _list_due_work_units(
        self,
        *,
        now: datetime,
        limit: int,
        provider_bound_only: bool,
    ) -> tuple[WorkUnitRecord, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        timestamp = now.isoformat()
        terminal = (
            *TERMINAL_WORK_UNIT_VALUES,
            WorkUnitStatus.WAITING_INPUT.value,
        )
        provider_filter = (
            "AND w.provider_kind IS NOT NULL "
            "AND w.provider_task_id IS NOT NULL "
            if provider_bound_only
            else ""
        )
        with sqlite_transaction(self.db_path) as connection:
            rows = connection.execute(
                "SELECT w.owner_ref, w.execution_id, w.work_unit_id "
                "FROM execution_work_units w JOIN runs r "
                "ON r.user_id = w.owner_ref "
                "AND r.execution_id = w.execution_id "
                "WHERE w.status NOT IN (?, ?, ?, ?, ?, ?) "
                + provider_filter
                + "AND (w.next_attempt_at IS NULL OR w.next_attempt_at <= ?) "
                "AND (w.lease_expires_at IS NULL OR w.lease_expires_at <= ?) "
                "AND r.execution_terminal_outcome IS NULL "
                "AND r.execution_tombstoned_at IS NULL "
                "ORDER BY COALESCE(w.next_attempt_at, w.created_at), "
                "w.updated_at, w.work_unit_id LIMIT ?",
                (*terminal, timestamp, timestamp, limit),
            ).fetchall()
        return tuple(
            self.get_work_unit(execution_id, work_unit_id, owner=owner)
            for owner, execution_id, work_unit_id in rows
        )

    def execution_deadline_at(
        self,
        execution_id: str,
        *,
        owner: str,
    ) -> datetime:
        """Return the durable execution deadline without advancing state."""
        with sqlite_transaction(self.db_path) as connection:
            row = connection.execute(
                "SELECT execution_deadline_at FROM runs WHERE user_id = ? "
                "AND execution_id = ? AND execution_tombstoned_at IS NULL",
                (owner, execution_id),
            ).fetchone()
        if row is None:
            raise ExecutionWorkNotFoundError(execution_id)
        value = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ExecutionWorkInvariantError("naive_execution_deadline")
        return value

    def update_work_unit_status(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        status: WorkUnitStatus | str,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """CAS-update the current state of one logical work unit."""
        status = WorkUnitStatus(status)
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            self._update_work_unit(
                connection,
                owner=owner,
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=expected_revision,
                updates={"status": status.value},
                updated_at=now,
            )
            connection.commit()
        return self.get_work_unit(execution_id, work_unit_id, owner=owner)

    def claim_lease(
        self,
        execution_id: str,
        work_unit_id: str,
        **fields: Unpack[WorkLeaseClaimFields],
    ) -> WorkUnitRecord:
        """CAS-acquire or renew a bounded worker lease."""
        if fields["lease_seconds"] < 1:
            raise ValueError("lease_seconds must be positive")
        now = self._clock()
        expires = (
            now + timedelta(seconds=fields["lease_seconds"])
        ).isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, fields["owner"], execution_id)
            current = self._read_work_unit(
                connection,
                fields["owner"],
                execution_id,
                work_unit_id,
            )
            if current is None:
                raise ExecutionWorkNotFoundError(work_unit_id)
            if current.lease_owner not in {
                None,
                fields["worker_id"],
            } and _is_future(current.lease_expires_at, now):
                raise ExecutionWorkConflictError("lease_held")
            self._update_work_unit(
                connection,
                owner=fields["owner"],
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=fields["expected_revision"],
                updates={
                    "lease_owner": fields["worker_id"],
                    "lease_expires_at": expires,
                },
                updated_at=now.isoformat(),
            )
            connection.commit()
        return self.get_work_unit(
            execution_id,
            work_unit_id,
            owner=fields["owner"],
        )

    def release_lease(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        worker_id: str,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """Release only a lease currently owned at the expected revision."""
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE execution_work_units SET lease_owner = NULL, "
                "lease_expires_at = NULL, updated_at = ?, "
                "revision = revision + 1 WHERE owner_ref = ? "
                "AND execution_id = ? AND work_unit_id = ? "
                "AND lease_owner = ? AND revision = ?",
                (
                    now,
                    owner,
                    execution_id,
                    work_unit_id,
                    worker_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                self._raise_missing_or_conflict(
                    connection,
                    table="execution_work_units",
                    id_column="work_unit_id",
                    owner=owner,
                    execution_id=execution_id,
                    identity=work_unit_id,
                )
            connection.commit()
        return self.get_work_unit(execution_id, work_unit_id, owner=owner)

    def schedule_retry(
        self,
        execution_id: str,
        work_unit_id: str,
        **fields: Unpack[WorkRetryFields],
    ) -> WorkUnitRecord:
        """Release one owned lease into a bounded durable retry state."""
        if not fields["error_code"] or len(fields["error_code"]) > 128:
            raise ValueError("bounded error_code is required")
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE execution_work_units SET status = ?, "
                "next_attempt_at = ?, last_error_code = ?, "
                "lease_owner = NULL, lease_expires_at = NULL, "
                "updated_at = ?, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND work_unit_id = ? "
                "AND lease_owner = ? AND revision = ?",
                (
                    WorkUnitStatus.RETRY_SCHEDULED.value,
                    fields["next_attempt_at"].isoformat(),
                    fields["error_code"],
                    now,
                    fields["owner"],
                    execution_id,
                    work_unit_id,
                    fields["worker_id"],
                    fields["expected_revision"],
                ),
            )
            if result.rowcount != 1:
                self._raise_missing_or_conflict(
                    connection,
                    table="execution_work_units",
                    id_column="work_unit_id",
                    owner=fields["owner"],
                    execution_id=execution_id,
                    identity=work_unit_id,
                )
            connection.commit()
        return self.get_work_unit(
            execution_id,
            work_unit_id,
            owner=fields["owner"],
        )

    def start_attempt(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """Advance a retryable work unit within its attempt budget."""
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_work_unit(
                connection, owner, execution_id, work_unit_id
            )
            if current is None:
                raise ExecutionWorkNotFoundError(work_unit_id)
            if current.attempt >= current.max_attempts:
                raise ExecutionWorkInvariantError("attempt_budget_exhausted")
            self._update_work_unit(
                connection,
                owner=owner,
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=expected_revision,
                updates={
                    "attempt": current.attempt + 1,
                    "status": WorkUnitStatus.PENDING.value,
                    "next_attempt_at": None,
                },
                updated_at=now,
            )
            connection.commit()
        return self.get_work_unit(execution_id, work_unit_id, owner=owner)

    def bind_provider(
        self,
        execution_id: str,
        work_unit_id: str,
        **binding: Unpack[ProviderBindingFields],
    ) -> WorkUnitRecord:
        """CAS-bind an immutable provider task and monotonic revision."""
        owner = binding["owner"]
        provider_kind = binding["provider_kind"]
        provider_task_id = binding["provider_task_id"]
        provider_revision = binding["provider_revision"]
        expected_revision = binding["expected_revision"]
        if provider_revision < 0:
            raise ValueError("provider_revision must be non-negative")
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_work_unit(
                connection, owner, execution_id, work_unit_id
            )
            if current is None:
                raise ExecutionWorkNotFoundError(work_unit_id)
            if (
                current.provider_task_id is not None
                and current.provider_task_id != provider_task_id
            ):
                raise ExecutionWorkConflictError("provider_identity_conflict")
            if provider_revision < current.provider_revision:
                raise ExecutionWorkConflictError("stale_provider_revision")
            self._update_work_unit(
                connection,
                owner=owner,
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=expected_revision,
                updates={
                    "provider_kind": provider_kind,
                    "provider_task_id": provider_task_id,
                    "provider_revision": provider_revision,
                },
                updated_at=now,
            )
            connection.commit()
        return self.get_work_unit(execution_id, work_unit_id, owner=owner)

    def set_cancellation_state(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        state: CancellationState,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """CAS-update the finite cancellation outcome for a work unit."""
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_work_unit(
                connection,
                owner=owner,
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=expected_revision,
                updates={"cancellation_state": state},
                updated_at=now,
            )
            connection.commit()
        return self.get_work_unit(execution_id, work_unit_id, owner=owner)

    def update_provider_trace_state(
        self,
        execution_id: str,
        work_unit_id: str,
        **fields: Unpack[ProviderTraceStateFields],
    ) -> WorkUnitRecord:
        """Commit bounded private provider-trace checkpoint state."""
        candidate = WorkUnitRecord.model_validate(
            {
                **self.get_work_unit(
                    execution_id,
                    work_unit_id,
                    owner=fields["owner"],
                ).model_dump(),
                "provider_trace_cursor": fields["cursor"],
                "provider_trace_revision": fields["source_revision"],
                "provider_trace_adapter_version": fields["adapter_version"],
                "provider_trace_overlap_identities": fields[
                    "overlap_identities"
                ],
                "provider_trace_contact_at": fields["contact_at"],
                "provider_trace_health": fields["health"],
            }
        )
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read_work_unit(
                connection,
                fields["owner"],
                execution_id,
                work_unit_id,
            )
            if current is None:
                raise ExecutionWorkNotFoundError(work_unit_id)
            if (
                current.provider_trace_adapter_version
                == fields["adapter_version"]
                and fields["source_revision"] < current.provider_trace_revision
            ):
                raise ExecutionWorkConflictError(
                    "stale_provider_trace_revision"
                )
            provider_contact_at = candidate.provider_trace_contact_at
            if current.provider_trace_contact_at is not None and (
                provider_contact_at is None
                or _parse_contact_at(current.provider_trace_contact_at)
                >= _parse_contact_at(provider_contact_at)
            ):
                provider_contact_at = current.provider_trace_contact_at
            self._update_work_unit(
                connection,
                owner=fields["owner"],
                execution_id=execution_id,
                work_unit_id=work_unit_id,
                expected_revision=fields["expected_revision"],
                updates={
                    "provider_trace_cursor": candidate.provider_trace_cursor,
                    "provider_trace_revision": (
                        candidate.provider_trace_revision
                    ),
                    "provider_trace_adapter_version": (
                        candidate.provider_trace_adapter_version
                    ),
                    "provider_trace_overlap_json": json.dumps(
                        candidate.provider_trace_overlap_identities,
                        separators=(",", ":"),
                    ),
                    "provider_trace_contact_at": provider_contact_at,
                    "provider_trace_health": candidate.provider_trace_health,
                },
                updated_at=now,
            )
            connection.commit()
        return self.get_work_unit(
            execution_id,
            work_unit_id,
            owner=fields["owner"],
        )

    def observe_provider_contact(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        observed_at: str,
    ) -> bool:
        """Refresh private provider liveness without claiming work revision."""
        candidate = _parse_contact_at(observed_at)
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            row = connection.execute(
                "SELECT provider_trace_contact_at FROM execution_work_units "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND work_unit_id = ?",
                (owner, execution_id, work_unit_id),
            ).fetchone()
            if row is None:
                raise ExecutionWorkNotFoundError(work_unit_id)
            if row[0] is not None and _parse_contact_at(row[0]) >= candidate:
                connection.commit()
                return False
            connection.execute(
                "UPDATE execution_work_units SET "
                "provider_trace_contact_at = ? "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND work_unit_id = ?",
                (observed_at, owner, execution_id, work_unit_id),
            )
            connection.commit()
        return True

    def latest_provider_contact_at(
        self,
        execution_id: str,
        *,
        owner: str,
    ) -> str | None:
        """Return the newest private contact clock for one owned execution."""
        with sqlite_transaction(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            rows = connection.execute(
                "SELECT provider_trace_contact_at FROM execution_work_units "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND provider_trace_contact_at IS NOT NULL",
                (owner, execution_id),
            ).fetchall()
        if not rows:
            return None
        return max((row[0] for row in rows), key=_parse_contact_at)

    @staticmethod
    def _authorize(
        connection: sqlite3.Connection, owner: str, execution_id: str
    ) -> None:
        if not execution_is_live(connection, owner, execution_id):
            raise ExecutionWorkNotFoundError(execution_id)

    @staticmethod
    def _read_span(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
        span_id: str,
    ) -> SpanRecord | None:
        row = connection.execute(
            "SELECT owner_ref, execution_id, span_id, parent_span_id, "
            "work_unit_id, kind, label_key, status, attempt, join_policy, "
            "started_at, last_activity_at, ended_at, revision "
            "FROM execution_spans WHERE owner_ref = ? AND execution_id = ? "
            "AND span_id = ?",
            (owner, execution_id, span_id),
        ).fetchone()
        if row is None:
            return None
        return SpanRecord(
            owner=row[0],
            execution_id=row[1],
            span_id=row[2],
            parent_span_id=row[3],
            work_unit_id=row[4],
            kind=row[5],
            label_key=row[6],
            status=row[7],
            attempt=row[8],
            join_policy=row[9],
            started_at=row[10],
            last_activity_at=row[11],
            ended_at=row[12],
            revision=row[13],
        )

    @staticmethod
    def _read_work_unit(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
        work_unit_id: str,
    ) -> WorkUnitRecord | None:
        row = connection.execute(
            "SELECT owner_ref, execution_id, work_unit_id, parent_span_id, "
            "operation_key, driver, status, join_policy, attempt, "
            "max_attempts, next_attempt_at, lease_owner, lease_expires_at, "
            "provider_kind, provider_task_id, provider_revision, "
            "provider_trace_cursor, provider_trace_revision, "
            "provider_trace_adapter_version, provider_trace_overlap_json, "
            "provider_trace_contact_at, provider_trace_health, "
            "cancellation_state, deadline_at, last_error_code, created_at, "
            "updated_at, revision FROM execution_work_units "
            "WHERE owner_ref = ? AND execution_id = ? AND work_unit_id = ?",
            (owner, execution_id, work_unit_id),
        ).fetchone()
        if row is None:
            return None
        return WorkUnitRecord(
            owner=row[0],
            execution_id=row[1],
            work_unit_id=row[2],
            parent_span_id=row[3],
            operation_key=row[4],
            driver=row[5],
            status=row[6],
            join_policy=row[7],
            attempt=row[8],
            max_attempts=row[9],
            next_attempt_at=row[10],
            lease_owner=row[11],
            lease_expires_at=row[12],
            provider_kind=row[13],
            provider_task_id=row[14],
            provider_revision=row[15],
            provider_trace_cursor=row[16],
            provider_trace_revision=row[17],
            provider_trace_adapter_version=row[18],
            provider_trace_overlap_identities=_decode_trace_overlap(row[19]),
            provider_trace_contact_at=row[20],
            provider_trace_health=row[21],
            cancellation_state=row[22],
            deadline_at=row[23],
            last_error_code=row[24],
            created_at=row[25],
            updated_at=row[26],
            revision=row[27],
        )

    def _update_work_unit(
        self,
        connection: sqlite3.Connection,
        **fields: Unpack[WorkUnitUpdateFields],
    ) -> None:
        allowed = {
            "attempt",
            "status",
            "next_attempt_at",
            "lease_owner",
            "lease_expires_at",
            "provider_kind",
            "provider_task_id",
            "provider_revision",
            "cancellation_state",
        } | _PROVIDER_TRACE_UPDATE_COLUMNS
        updates = fields["updates"]
        if not updates or not set(updates).issubset(allowed):
            raise ExecutionWorkInvariantError("invalid_work_unit_update")
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = [*updates.values(), fields["updated_at"]]
        result = connection.execute(
            f"UPDATE execution_work_units SET {assignments}, updated_at = ?, "
            "revision = revision + 1 WHERE owner_ref = ? AND execution_id = ? "
            "AND work_unit_id = ? AND revision = ?",
            (
                *values,
                fields["owner"],
                fields["execution_id"],
                fields["work_unit_id"],
                fields["expected_revision"],
            ),
        )
        if result.rowcount != 1:
            self._raise_missing_or_conflict(
                connection,
                table="execution_work_units",
                id_column="work_unit_id",
                owner=fields["owner"],
                execution_id=fields["execution_id"],
                identity=fields["work_unit_id"],
            )

    @staticmethod
    def _raise_missing_or_conflict(
        connection: sqlite3.Connection,
        **fields: Unpack[MissingOrConflictFields],
    ) -> None:
        found = connection.execute(
            f"SELECT 1 FROM {fields['table']} WHERE owner_ref = ? "
            f"AND execution_id = ? AND {fields['id_column']} = ?",
            (fields["owner"], fields["execution_id"], fields["identity"]),
        ).fetchone()
        if found is None:
            raise ExecutionWorkNotFoundError(fields["identity"])
        raise ExecutionWorkConflictError("revision_conflict")


def _work_spec(record: WorkUnitRecord) -> WorkUnitSpec:
    return WorkUnitSpec.model_validate(
        record.model_dump(
            include={
                "owner",
                "execution_id",
                "work_unit_id",
                "parent_span_id",
                "operation_key",
                "driver",
                "join_policy",
                "max_attempts",
                "deadline_at",
            }
        )
    )


def _parse_contact_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionWorkInvariantError(
            "invalid_provider_contact_at"
        ) from exc
    if parsed.utcoffset() is None:
        raise ExecutionWorkInvariantError("invalid_provider_contact_at")
    return parsed


def _decode_trace_overlap(raw: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExecutionWorkInvariantError(
            "invalid_provider_trace_overlap"
        ) from exc
    if not isinstance(decoded, list) or any(
        not isinstance(identity, str) for identity in decoded
    ):
        raise ExecutionWorkInvariantError("invalid_provider_trace_overlap")
    return tuple(decoded)


def _is_future(value: str | None, now: datetime) -> bool:
    if value is None:
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")) > now
