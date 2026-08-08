# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private, durable SQLite state for bounded Research coordination."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from importlib import import_module
from sqlite3 import Connection, Cursor, Row
from typing import Any, cast

from .research_input_store_support import _ResearchInputStoreBindings
from .research_input_types import (
    ResearchAdmissionReservation,
    ResearchWorkUnitRecord,
)
from .sqlite import sqlite_transaction


def _split_words(value: str, separator: str | None = None) -> tuple[str, ...]:
    return tuple(value.split(separator))


RESEARCH_WORK_LEASE = timedelta(seconds=60)
RESEARCH_HEARTBEAT_INTERVAL = timedelta(seconds=20)
RESEARCH_RECOVERY_BATCH_SIZE = 32
RESEARCH_SCHEMA_VERSION = 1
RESEARCH_SCHEMA_VERSION_TABLE = "research_input_schema_version"
RESEARCH_OPERATION = "research_input_resolution_v1"
PUBLIC_RESEARCH_STAGES = frozenset(
    _split_words("input_resolution planning execution report_assembly")
)
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_WORK_STATES = frozenset(
    _split_words(
        "pending leased sent succeeded retryable_failed terminal_failed "
        "ambiguous cancelled"
    )
)


class ResearchInputStore(_ResearchInputStoreBindings):
    """Store durable private Research coordination state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.initialize()

    def initialize(self) -> None:
        """Initialize additive private schema."""
        with sqlite_transaction(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            _add_public_run_columns(connection)
            _ensure_schema_version(connection)
            _ensure_private_tables(connection)
            _ensure_indexes(connection)
            _set_schema_version(connection)

    def add_work_unit(self, record: ResearchWorkUnitRecord) -> None:
        """Add a work unit exactly once."""
        now = _utc_iso(datetime.now(UTC))
        with sqlite_transaction(self.db_path) as connection:
            values = _work_insert_values(record, now)
            connection.execute(_WORK_INSERT_SQL, values)

    def reserve_admission(
        self, **request: Any
    ) -> ResearchAdmissionReservation | None:
        """Reserve one admission atomically."""
        with sqlite_transaction(self.db_path, timeout=30.0) as connection:
            connection.row_factory = Row
            now = _utc_iso(datetime.now(UTC))
            try:
                connection.execute("BEGIN IMMEDIATE")
                found, replay = _read_existing_admission(connection, request)
                if found:
                    return replay
                alias_digest = request.get("header_alias_digest")
                if alias_digest is not None and _alias_is_bound(
                    connection, request["owner"], alias_digest
                ):
                    return None
                _insert_admission(connection, request, now)
                return ResearchAdmissionReservation(
                    request["run_id"], False, "running"
                )
            except sqlite3.IntegrityError:
                if connection.in_transaction:
                    connection.rollback()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    _found, replay = _read_existing_admission(
                        connection, request
                    )
                    return replay
                except sqlite3.IntegrityError:
                    if connection.in_transaction:
                        connection.rollback()
                    return None

    def lookup_admission(
        self,
        *,
        owner: str,
        identity_digest: str,
        header_alias_digest: str | None,
        client_fingerprint: str,
    ) -> tuple[bool, ResearchAdmissionReservation | None]:
        """Look up an exact admission replay."""
        request = {
            "owner": owner,
            "identity_digest": identity_digest,
            "header_alias_digest": header_alias_digest,
            "client_fingerprint": client_fingerprint,
        }
        with sqlite_transaction(self.db_path, timeout=30.0) as connection:
            connection.row_factory = Row
            connection.execute("BEGIN IMMEDIATE")
            found, replay = _read_existing_admission(connection, request)
            alias_bound = header_alias_digest is not None and _alias_is_bound(
                connection, owner, header_alias_digest
            )
            if not found and alias_bound:
                found = True
            return found, replay

    def persist_resolution(self, run_id: str, **fields: Any) -> bool:
        """Persist structured resolution state."""
        expected_revision = fields.pop("expected_revision", None)
        status = fields.pop("status", "pending")
        if not isinstance(status, str) or status in TERMINAL_RUN_STATUSES:
            raise ValueError("resolution state must be nonterminal")
        values = _resolution_values(run_id, status, fields)
        with sqlite_transaction(self.db_path) as connection:
            if expected_revision is None:
                cursor = connection.execute(
                    _RESOLUTION_INSERT_SQL, (*values, run_id)
                )
                return cursor.rowcount == 1
            cursor = connection.execute(
                _RESOLUTION_UPDATE_SQL,
                (*values[1:19], values[20], run_id, expected_revision),
            )
            return cursor.rowcount == 1

    def persist_plan_and_outbox(
        self, run_id: str, expected_revision: int, **values: Any
    ) -> tuple[dict[str, Any], ...] | None:
        """Atomically enqueue a validated Research child plan."""
        module = import_module(
            "mcp_server_phytomni.agents.research.dispatch_outbox"
        )
        persist = getattr(module, "_persist_plan_in_store")
        return persist(self, run_id, expected_revision, **values)

    def load_resolution(self, run_id: str) -> dict[str, Any] | None:
        """Load structured resolution state."""
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = Row
            row = connection.execute(
                "SELECT * FROM research_input_resolutions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in _split_words(
            "source_map_json candidates_json managed_snapshot_json "
            "inventory_json final_projection_json"
        ):
            if result.get(name) is not None:
                result[name] = json.loads(result[name])
        return result

    def claim_work(
        self,
        unit_id: str,
        lease_owner: str,
        now: datetime,
        ttl: timedelta = RESEARCH_WORK_LEASE,
    ) -> ResearchWorkUnitRecord | None:
        """Claim eligible work with CAS."""
        now_iso, expires_at = _utc_iso(now), _utc_iso(now + ttl)
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = Row
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
            if record is None or not _claimable(record, now_iso):
                return None
            cursor = _work_update(
                connection,
                "state = 'leased', lease_owner = ?, lease_expires_at = ?, "
                "attempt = attempt + 1, updated_at = ?, "
                "revision = revision + 1",
                "unit_id = ? AND revision = ? AND state = ?",
                (
                    lease_owner,
                    expires_at,
                    now_iso,
                    unit_id,
                    record["revision"],
                    record["state"],
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
            return _to_record(claimed) if claimed is not None else None

    def heartbeat_work(
        self,
        unit_id: str,
        lease_owner: str,
        revision: int,
        now: datetime,
    ) -> ResearchWorkUnitRecord | None:
        """Renew an active work lease."""
        now_iso, expiry = _utc_iso(now), _utc_iso(now + RESEARCH_WORK_LEASE)
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = Row
            cursor = _work_update(
                connection,
                "lease_expires_at = ?, updated_at = ?, "
                "revision = revision + 1",
                "unit_id = ? AND lease_owner = ? AND revision = ? "
                "AND state IN ('leased', 'sent')",
                (expiry, now_iso, unit_id, lease_owner, revision),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
        return _to_record(row) if row is not None else None

    def mark_sent(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        **options: object,
    ) -> int | None:
        """Commit sent state and provider identity."""
        values = tuple(
            cast(str | None, options.get(name))
            for name in _PROVIDER_DIGEST_NAMES
        )
        timestamp = _utc_iso(
            cast(datetime | None, options.get("now")) or datetime.now(UTC)
        )
        parameters: tuple[object, ...] = (
            *values,
            timestamp,
            timestamp,
            unit_id,
            lease_owner,
            expected_revision,
        )
        changed = _execute_work_update(
            self.db_path,
            _MARK_SENT_ASSIGNMENTS,
            _MARK_SENT_PREDICATE,
            parameters,
        )
        return expected_revision + 1 if changed == 1 else None

    def settle_validated(
        self,
        unit_id: str,
        lease_owner: str,
        *args: object,
        **options: object,
    ) -> bool:
        """Settle a validated provider output."""
        expected_revision, output, now = (
            args[0] if args else options["expected_revision"],
            args[1] if len(args) > 1 else options["output"],
            args[2] if len(args) > 2 else options.get("now"),
        )
        timestamp = _utc_iso(cast(datetime | None, now) or datetime.now(UTC))
        parameters: tuple[object, ...] = (
            _canonical_json(output),
            timestamp,
            timestamp,
            unit_id,
            lease_owner,
            expected_revision,
        )
        changed = _execute_work_update(
            self.db_path,
            _SETTLE_ASSIGNMENTS,
            _SETTLE_PREDICATE,
            parameters,
        )
        return changed == 1

    def complete_work(
        self,
        record: ResearchWorkUnitRecord,
        state: str,
        *args: object,
        **options: object,
    ) -> bool:
        """Complete leased or sent work with CAS."""
        now = args[0] if args else options["now"]
        names = ("output", "failure_code", "failure_retryable")
        output, failure_code, failure_retryable = (
            args[index] if len(args) > index else options.get(name)
            for index, name in enumerate(names, 1)
        )
        if state not in _WORK_STATES - {"pending", "leased", "sent"}:
            raise ValueError(f"unsupported completion state: {state}")
        now_iso = _utc_iso(cast(datetime, now))
        expiry = record.lease_expires_at
        retryable = (
            None
            if failure_retryable is None
            else int(cast(bool | int, failure_retryable))
        )
        parameters: tuple[object, ...] = (
            state,
            state,
            expiry and _utc_iso(expiry),
            _canonical_json(output) if output is not None else None,
            failure_code,
            retryable,
            now_iso,
            now_iso,
            record.unit_id,
            record.lease_owner,
            record.revision,
        )
        changed = _execute_work_update(
            self.db_path,
            _COMPLETE_ASSIGNMENTS,
            _COMPLETE_PREDICATE,
            parameters,
        )
        return changed == 1

    def purge_run(self, run_id: str) -> None:
        """Purge private and public run rows."""
        with sqlite_transaction(self.db_path) as connection:
            purge_research_children(connection, (run_id,))
            connection.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _add_public_run_columns(connection: Connection) -> None:
    existing = _table_columns(connection, "runs")
    if not existing:
        return
    for name, definition in (
        ("stage", "TEXT"),
        ("failure_json", "TEXT"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in existing:
            connection.execute(
                f"ALTER TABLE runs ADD COLUMN {name} {definition}"
            )


def _ensure_schema_version(connection: Connection) -> None:
    connection.execute(_CREATE_SCHEMA_VERSION_DDL)
    columns = _table_columns(connection, RESEARCH_SCHEMA_VERSION_TABLE)
    if columns != {"id", "version"}:
        raise sqlite3.DatabaseError("malformed research schema version table")
    row = connection.execute(
        f"SELECT version FROM {RESEARCH_SCHEMA_VERSION_TABLE} WHERE id = 1"
    ).fetchone()
    if row is not None and int(row[0]) > RESEARCH_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            "research schema is newer than this worker"
        )


def _ensure_private_tables(connection: Connection) -> None:
    for table, columns, constraints in _PRIVATE_TABLES:
        connection.execute(_create_table_ddl(table, columns, constraints))
        _add_missing_columns(connection, table, columns)
    connection.execute(
        "UPDATE research_work_units SET created_at = COALESCE("
        "created_at, CURRENT_TIMESTAMP), "
        "updated_at = COALESCE(updated_at, created_at)"
    )


def _add_missing_columns(
    connection: Connection,
    table: str,
    definitions: Sequence[tuple[str, str]],
) -> None:
    existing = _table_columns(connection, table)
    for name, definition in definitions:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


def _ensure_indexes(connection: Connection) -> None:
    for name in (
        "uq_research_binding_owner_identity",
        "uq_research_binding_owner_alias",
        "uq_research_success_input_policy",
    ):
        connection.execute(f"DROP INDEX IF EXISTS {name}")
    for statement in _INDEX_DDLS:
        connection.execute(statement)


def _set_schema_version(connection: Connection) -> None:
    connection.execute(
        f"INSERT INTO {RESEARCH_SCHEMA_VERSION_TABLE}(id, version) VALUES "
        "(1, ?) ON CONFLICT(id) DO UPDATE SET version = excluded.version",
        (RESEARCH_SCHEMA_VERSION,),
    )


def purge_research_children(
    connection: Connection, run_ids: Sequence[str]
) -> None:
    """Purge private children before their run owner."""
    ids = tuple(run_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    tables = dict.fromkeys(
        _split_words(
            "research_dispatch_outbox research_work_units "
            "research_input_resolutions research_idempotency_bindings"
        ),
        "run_id",
    )
    tables["research_object_grants"] = "parent_run_id"
    for table, column in tables.items():
        if column in _table_columns(connection, table):
            connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})", ids
            )


def _claimable(row: Row, now_iso: str) -> bool:
    state, expiry = row["state"], row["lease_expires_at"]
    if state == "pending":
        return True
    if expiry is None or expiry > now_iso:
        return False
    return state == "retryable_failed" or (
        state == "leased"
        and row["sent_at"] is None
        and row["provider_request_digest"] is None
    )


_WORK_SELECT = "SELECT * FROM research_work_units WHERE unit_id = ?"
_PROVIDER_DIGEST_NAMES = _split_words(
    "provider_request_digest provider_idempotency_digest "
    "execution_fingerprint evidence_digest"
)
_MARK_SENT_ASSIGNMENTS = (
    "state='sent',provider_request_digest=COALESCE(?,provider_request_digest),"
    "provider_idempotency_digest=COALESCE(?,provider_idempotency_digest),"
    "execution_fingerprint=COALESCE(?,execution_fingerprint),"
    "evidence_digest=COALESCE(?,evidence_digest),sent_at=?,updated_at=?,"
    "revision=revision+1"
)
_MARK_SENT_PREDICATE = (
    "unit_id = ? AND lease_owner = ? AND revision = ? AND state = 'leased'"
)
_SETTLE_ASSIGNMENTS = (
    "state = 'succeeded', output_json = ?, failure_code = NULL, "
    "failure_retryable = NULL, lease_owner = NULL, lease_expires_at = NULL, "
    "updated_at = ?, completed_at = ?, revision = revision + 1"
)
_SETTLE_PREDICATE = (
    "unit_id = ? AND lease_owner = ? AND revision = ? AND state = 'sent'"
)
_COMPLETE_ASSIGNMENTS = (
    "state = ?, lease_owner = NULL, lease_expires_at = CASE WHEN ? = "
    "'retryable_failed' THEN ? ELSE NULL END, output_json = COALESCE(?, "
    "output_json), failure_code = ?, failure_retryable = ?, updated_at = ?, "
    "completed_at = ?, revision = revision + 1"
)
_COMPLETE_PREDICATE = (
    "unit_id = ? AND lease_owner = ? AND revision = ? "
    "AND state IN ('leased', 'sent')"
)
_PARENT_LIVE = (
    "EXISTS (SELECT 1 FROM runs WHERE runs.run_id=research_work_units.run_id "
    "AND runs.status NOT IN ('succeeded','failed','cancelled')) AND COALESCE("
    "(SELECT cancel_requested FROM research_input_resolutions WHERE run_id="
    "research_work_units.run_id),0)=0"
)
_WORK_INSERT_FIELDS = _split_words(
    "unit_id run_id kind state input_digest policy_digest lease_owner "
    "lease_expires_at attempt revision schema_version provider_request_digest "
    "provider_idempotency_digest execution_fingerprint evidence_digest "
    "output_json "
    "failure_code failure_retryable sent_at completed_at created_at updated_at"
)
_WORK_INSERT_SQL = (
    "INSERT INTO research_work_units ("
    + ",".join(_WORK_INSERT_FIELDS)
    + ") SELECT "
    + ",".join("?" for _ in _WORK_INSERT_FIELDS)
    + " WHERE EXISTS (SELECT 1 FROM runs WHERE runs.run_id=? AND runs.status "
    + "NOT IN ('succeeded','failed','cancelled')) AND NOT EXISTS (SELECT 1 "
    + "FROM research_input_resolutions WHERE run_id=? AND COALESCE("
    + "cancel_requested,0)<>0)"
)
_RESOLUTION_FIELDS = _split_words(
    "run_id schema_version status revision original_query_digest "
    "original_query_length effective_query source_map_json candidates_json "
    "managed_snapshot_json effective_query_digest source_map_digest "
    "candidate_digest snapshot_digest evidence_digest work_digest "
    "client_fingerprint execution_fingerprint policy_digest plan_digest "
    "created_at updated_at"
)
_RESOLUTION_UPDATE_FIELDS = (
    _RESOLUTION_FIELDS[1:3]
    + _RESOLUTION_FIELDS[4:-2]
    + _RESOLUTION_FIELDS[-1:]
)
_RESOLUTION_INSERT_SQL = (
    "INSERT OR IGNORE INTO research_input_resolutions("
    + ",".join(_RESOLUTION_FIELDS)
    + ")SELECT ?,?,?,0,"
    + ",".join("?" for _ in _RESOLUTION_FIELDS[4:])
    + " WHERE EXISTS (SELECT 1 FROM runs WHERE runs.run_id=? AND runs.status "
    + "NOT IN ('succeeded','failed','cancelled'))"
)
_RESOLUTION_UPDATE_SQL = (
    "UPDATE research_input_resolutions SET revision=revision+1,"
    + ",".join(f"{name}=?" for name in _RESOLUTION_UPDATE_FIELDS)
    + " WHERE run_id=? AND revision=? AND EXISTS (SELECT 1 FROM runs WHERE "
    + "runs.run_id=research_input_resolutions.run_id AND runs.status NOT IN "
    + "('succeeded','failed','cancelled')) AND COALESCE(cancel_requested,0)=0"
)


def _work_insert_values(
    record: ResearchWorkUnitRecord, now: str
) -> tuple[object, ...]:
    identity = tuple(
        getattr(record, name)
        for name in _split_words(
            "unit_id run_id kind state input_digest policy_digest lease_owner"
        )
    )
    output = None if record.output is None else _canonical_json(record.output)
    retryable = record.failure_retryable
    return identity + (
        _utc_iso(record.lease_expires_at) if record.lease_expires_at else None,
        record.attempt,
        record.revision,
        RESEARCH_SCHEMA_VERSION,
        *(getattr(record, name) for name in _PROVIDER_DIGEST_NAMES),
        output,
        record.failure_code,
        None if retryable is None else int(retryable),
        _utc_iso(record.sent_at) if record.sent_at else None,
        _utc_iso(record.completed_at) if record.completed_at else None,
        now,
        now,
        record.run_id,
        record.run_id,
    )


def _work_update(
    connection: Connection,
    assignments: str,
    predicate: str,
    parameters: tuple[object, ...],
) -> Cursor:
    return connection.execute(
        f"UPDATE research_work_units SET {assignments} WHERE {predicate} "
        f"AND {_PARENT_LIVE}",
        parameters,
    )


def _execute_work_update(
    db_path: str,
    assignments: str,
    predicate: str,
    parameters: tuple[object, ...],
) -> int:
    with sqlite_transaction(db_path) as connection:
        return _work_update(
            connection, assignments, predicate, parameters
        ).rowcount


_WORK_RECORD_FIELDS = _split_words(
    "unit_id run_id kind state input_digest policy_digest lease_owner "
    "attempt revision provider_request_digest provider_idempotency_digest "
    "execution_fingerprint evidence_digest failure_code"
)


def _to_record(row: Row) -> ResearchWorkUnitRecord:
    retryable = row["failure_retryable"]
    return ResearchWorkUnitRecord(
        **{name: row[name] for name in _WORK_RECORD_FIELDS},
        lease_expires_at=_parse_iso(row["lease_expires_at"]),
        output=_json_object(row["output_json"]),
        failure_retryable=None if retryable is None else bool(retryable),
        sent_at=_parse_iso(row["sent_at"]),
        completed_at=_parse_iso(row["completed_at"]),
    )


def _utc_iso(value: datetime) -> str:
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_object(value: object) -> dict[str, Any] | None:
    try:
        decoded = json.loads(value) if isinstance(value, str) else None
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _read_existing_admission(
    connection: Connection, request: Mapping[str, Any]
) -> tuple[bool, ResearchAdmissionReservation | None]:
    row = connection.execute(
        "SELECT run_id, client_fingerprint "
        "FROM research_idempotency_bindings "
        "WHERE owner = ? AND operation = ? AND idempotency_digest = ?",
        (
            request["owner"],
            RESEARCH_OPERATION,
            request["identity_digest"],
        ),
    ).fetchone()
    if row is None:
        return False, None
    if row["client_fingerprint"] != request["client_fingerprint"]:
        return True, None
    if not _attach_alias(
        connection,
        request["owner"],
        row["run_id"],
        request.get("header_alias_digest"),
    ):
        return True, None
    status = connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (row["run_id"],)
    ).fetchone()
    if status is None:
        return True, None
    return True, ResearchAdmissionReservation(row["run_id"], True, status[0])


def _insert_admission(
    connection: Connection, request: Mapping[str, Any], now: str
) -> None:
    connection.execute(
        _RUN_INSERT_SQL,
        (request["run_id"], request["owner"], now, now, request["locale"]),
    )
    connection.execute(
        _BINDING_INSERT_SQL,
        (
            request["run_id"],
            request["identity_digest"],
            request["original_query_digest"],
            RESEARCH_SCHEMA_VERSION,
            request["owner"],
            RESEARCH_OPERATION,
            request["identity_kind"],
            request.get("header_alias_digest"),
            request["client_fingerprint"],
            now,
            now,
        ),
    )
    _insert_resolution(connection, request, now)
    connection.execute(
        _ROOT_WORK_INSERT_SQL,
        (
            f"{request['run_id']}:resolve_root",
            request["run_id"],
            request["root_input_digest"],
            request["client_fingerprint"],
            RESEARCH_SCHEMA_VERSION,
            now,
            now,
        ),
    )


_RUN_INSERT_SQL = (
    "INSERT INTO runs (run_id, user_id, agent, origin, status, result_json, "
    "error, created_at, updated_at, expires_at, locale) VALUES (?, ?, "
    "'research', 'api', 'running', NULL, NULL, ?, ?, NULL, ?)"
)
_BINDING_INSERT_SQL = (
    "INSERT INTO research_idempotency_bindings (run_id, idempotency_digest, "
    "request_digest, schema_version, owner, operation, identity_kind, "
    "alias_digest, client_fingerprint, created_at, updated_at) VALUES "
    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_ROOT_WORK_INSERT_SQL = (
    "INSERT INTO research_work_units (unit_id, run_id, kind, state, "
    "input_digest, policy_digest, attempt, revision, schema_version, "
    "created_at, updated_at) "
    "VALUES (?, ?, 'resolve_root', 'pending', ?, ?, 0, 0, ?, ?, ?)"
)


def _insert_resolution(
    connection: Connection, request: Mapping[str, Any], now: str
) -> None:
    serialized = (
        request["effective_query"],
        *(
            _canonical_json(request[name])
            for name in ("source_map", "candidates", "managed_snapshot")
        ),
    )
    digests = tuple(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in serialized
    )
    connection.execute(
        _ROOT_RESOLUTION_INSERT_SQL,
        (
            request["run_id"],
            RESEARCH_SCHEMA_VERSION,
            request["original_query_digest"],
            request["original_query_length"],
            *serialized,
            *digests,
            request["client_fingerprint"],
            now,
            now,
        ),
    )


_ROOT_RESOLUTION_INSERT_SQL = (
    "INSERT INTO research_input_resolutions (run_id, schema_version, status, "
    "revision, original_query_digest, original_query_length, effective_query, "
    "source_map_json, candidates_json, managed_snapshot_json, "
    "effective_query_digest, source_map_digest, candidate_digest, "
    "snapshot_digest, evidence_digest, work_digest, client_fingerprint, "
    "created_at, updated_at) VALUES (?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, "
    "?, ?, ?, ?, '', '', ?, ?, ?)"
)


def _alias_is_bound(
    connection: Connection, owner: str, alias_digest: str
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM research_idempotency_bindings "
            "WHERE owner = ? AND operation = ? AND alias_digest = ?",
            (owner, RESEARCH_OPERATION, alias_digest),
        ).fetchone()
        is not None
    )


def _attach_alias(
    connection: Connection,
    owner: str,
    run_id: str,
    alias_digest: str | None,
) -> bool:
    if alias_digest is None:
        return True
    alias_row = connection.execute(
        "SELECT run_id FROM research_idempotency_bindings "
        "WHERE owner = ? AND operation = ? AND alias_digest = ?",
        (owner, RESEARCH_OPERATION, alias_digest),
    ).fetchone()
    if alias_row is not None and alias_row["run_id"] != run_id:
        return False
    binding = connection.execute(
        "SELECT alias_digest FROM research_idempotency_bindings "
        "WHERE run_id = ? AND owner = ? AND operation = ?",
        (run_id, owner, RESEARCH_OPERATION),
    ).fetchone()
    if binding is None:
        return False
    current_alias = binding["alias_digest"]
    if current_alias is not None:
        return current_alias == alias_digest
    connection.execute(
        "UPDATE research_idempotency_bindings SET alias_digest = ?, "
        "updated_at = ? WHERE run_id = ? AND owner = ? AND operation = ? "
        "AND alias_digest IS NULL",
        (
            alias_digest,
            _utc_iso(datetime.now(UTC)),
            run_id,
            owner,
            RESEARCH_OPERATION,
        ),
    )
    return True


def _resolution_values(
    run_id: str, status: str, fields: dict[str, Any]
) -> tuple[Any, ...]:
    required = _validated_resolution_fields(fields)
    serialized = tuple(_canonical_json(required[i]) for i in (3, 4, 5))
    digests = tuple(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in (required[2], *serialized)
    )
    now = _utc_iso(datetime.now(UTC))
    optional_names = _split_words(
        "client_fingerprint execution_fingerprint policy_digest plan_digest"
    )
    optional = tuple(fields.pop(name, None) for name in optional_names)
    if fields:
        raise TypeError(f"unexpected resolution fields: {sorted(fields)}")
    return (
        run_id,
        RESEARCH_SCHEMA_VERSION,
        status,
        *required[:3],
        *serialized,
        *digests,
        *required[6:8],
        *optional,
        now,
        now,
    )


def _validated_resolution_fields(fields: dict[str, Any]) -> tuple[Any, ...]:
    names = _split_words(
        "original_query_digest original_query_length effective_query "
        "source_map "
        "parsed_candidates managed_snapshot evidence_digest work_digest"
    )
    try:
        values = tuple(fields.pop(name) for name in names)
    except KeyError as error:
        raise TypeError(
            f"missing required resolution field: {error.args[0]}"
        ) from error
    if not isinstance(values[0], str):
        raise TypeError("original_query_digest must be text")
    if (
        not isinstance(values[1], int)
        or isinstance(values[1], bool)
        or values[1] < 0
    ):
        raise ValueError("original_query_length must be non-negative")
    if not isinstance(values[2], str):
        raise TypeError("effective_query must be text")
    if not isinstance(values[3], Mapping):
        raise TypeError("source_map must be a mapping")
    for index, name in ((4, "parsed_candidates"), (5, "managed_snapshot")):
        if not isinstance(values[index], Sequence) or isinstance(
            values[index], (str, bytes)
        ):
            raise TypeError(f"{name} must be a sequence")
    if not isinstance(values[6], str) or not isinstance(values[7], str):
        raise TypeError("evidence and work digests must be text")
    return values


def _table_columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _create_table_ddl(
    table: str, columns: Sequence[tuple[str, str]], constraints: str
) -> str:
    fields = ", ".join(f"{name} {definition}" for name, definition in columns)
    return f"CREATE TABLE IF NOT EXISTS {table} ({fields}{constraints})"


_CREATE_SCHEMA_VERSION_DDL = (
    f"CREATE TABLE IF NOT EXISTS {RESEARCH_SCHEMA_VERSION_TABLE} ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
)


def _column_definitions(spec: str) -> tuple[tuple[str, str], ...]:
    columns: list[tuple[str, str]] = []
    for token in spec.split():
        name, kind = token.split(":", 1)
        base, default = ("TEXT" if kind[0] == "T" else "INTEGER", kind[1:])
        columns.append(
            (name, f"{base} NOT NULL DEFAULT {default}" if default else base)
        )
    return tuple(columns)


_BINDING_COLUMNS = _column_definitions(
    """run_id:T idempotency_digest:T request_digest:T schema_version:I1
owner:T'' operation:T'research_input_resolution_v1' identity_kind:T'header'
alias_digest:T client_fingerprint:T conversation_key_digest:T created_at:T''
updated_at:T expires_at:T"""
)
_RESOLUTION_COLUMNS = _column_definitions(
    """run_id:T schema_version:I1 status:T'pending' revision:I0
cancel_requested:I0 original_query_digest:T original_query_length:I0
effective_query:T source_map_json:T candidates_json:T managed_snapshot_json:T
inventory_json:T client_fingerprint:T execution_fingerprint:T policy_digest:T
model_id:T effective_query_digest:T'' source_map_digest:T''
candidate_digest:T'' snapshot_digest:T'' evidence_digest:T'' work_digest:T''
coverage_total:I0 coverage_digest:T final_projection_json:T plan_digest:T
last_stage:T failure_code:T failure_retryable:I status_hint:T created_at:T''
updated_at:T expires_at:T"""
)
_WORK_COLUMNS = _column_definitions(
    """unit_id:T run_id:T kind:T'' state:T'pending' input_digest:T''
policy_digest:T'' lease_owner:T lease_expires_at:T attempt:I0 revision:I0
schema_version:I1 evidence_ids_json:T provider_request_digest:T
provider_idempotency_digest:T execution_fingerprint:T evidence_digest:T
output_json:T failure_code:T failure_retryable:I created_at:T updated_at:T
sent_at:T completed_at:T"""
)
_OUTBOX_COLUMNS = _column_definitions(
    """outbox_id:T run_id:T unit_id:T payload_digest:T'' state:T'pending'
attempt:I0 revision:I0 schema_version:I1 child_ordinal:I dispatch_fingerprint:T
parent_revision:I0
payload_json:T output_dir:T grant_ids_json:T snapshot_digest:T lease_owner:T
lease_expires_at:T remote_task_id:T failure_code:T failure_retryable:I
updated_at:T sent_at:T completed_at:T created_at:T''"""
)
_PRIVATE_TABLES = (
    (
        "research_idempotency_bindings",
        _BINDING_COLUMNS,
        ",PRIMARY KEY(run_id,idempotency_digest),FOREIGN KEY(run_id)"
        " REFERENCES runs(run_id)",
    ),
    (
        "research_input_resolutions",
        _RESOLUTION_COLUMNS,
        ",PRIMARY KEY(run_id),FOREIGN KEY(run_id)REFERENCES runs(run_id)",
    ),
    (
        "research_work_units",
        _WORK_COLUMNS,
        ",PRIMARY KEY(unit_id),FOREIGN KEY(run_id)REFERENCES runs(run_id)",
    ),
    (
        "research_dispatch_outbox",
        _OUTBOX_COLUMNS,
        ",PRIMARY KEY(outbox_id),FOREIGN KEY(run_id)REFERENCES runs(run_id),"
        "FOREIGN KEY(unit_id)REFERENCES research_work_units(unit_id)",
    ),
)
_INDEX_DDLS = _split_words(
    """CREATE UNIQUE INDEX IF NOT EXISTS
uq_research_binding_owner_identity ON research_idempotency_bindings
(owner,operation,idempotency_digest) WHERE owner<>''|CREATE UNIQUE INDEX IF NOT
EXISTS uq_research_binding_owner_alias ON research_idempotency_bindings
(owner,operation,alias_digest) WHERE owner<>'' AND alias_digest IS NOT NULL|
CREATE INDEX IF NOT EXISTS idx_research_binding_digest ON
research_idempotency_bindings(idempotency_digest)|CREATE INDEX IF NOT EXISTS
idx_research_work_claim ON research_work_units(state,lease_expires_at)|
CREATE INDEX IF NOT EXISTS idx_research_work_run
ON research_work_units(run_id)|CREATE INDEX IF NOT EXISTS
idx_research_outbox_pending ON research_dispatch_outbox(state,created_at)|
CREATE UNIQUE INDEX IF NOT EXISTS
uq_research_success_input_policy ON research_work_units
(run_id,kind,input_digest,policy_digest) WHERE state='succeeded'""",
    "|",
)
