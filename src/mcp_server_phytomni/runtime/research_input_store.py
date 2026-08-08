# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private, durable SQLite state for bounded Research coordination.

This module intentionally persists identifiers and integrity digests only.  It
does not provide a storage path for uploaded bodies, converted text, or a
second copy of the caller's original query.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .sqlite import sqlite_transaction

RESEARCH_WORK_LEASE = timedelta(seconds=60)
RESEARCH_HEARTBEAT_INTERVAL = timedelta(seconds=20)
RESEARCH_RECOVERY_BATCH_SIZE = 32
RESEARCH_SCHEMA_VERSION = 1
RESEARCH_SCHEMA_VERSION_TABLE = "research_input_schema_version"
PUBLIC_RESEARCH_STAGES = frozenset(
    {"input_resolution", "planning", "execution", "report_assembly"}
)
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

_WORK_STATES = frozenset(
    {
        "pending",
        "leased",
        "sent",
        "succeeded",
        "retryable_failed",
        "terminal_failed",
        "ambiguous",
        "cancelled",
    }
)
_ACTIVE_WORK_STATES = frozenset({"leased", "sent"})


@dataclass(frozen=True, slots=True)
class _ResearchWorkUnitIdentity:
    """Identity portion of a private coordinator work record."""

    unit_id: str
    run_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class ResearchWorkUnitRecord(_ResearchWorkUnitIdentity):
    """The lease-safe projection of one private coordinator work unit."""

    state: str
    input_digest: str
    policy_digest: str
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt: int
    revision: int


class ResearchInputStore:
    """Own versioned Research coordinator rows colocated with run state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.initialize()

    def initialize(self) -> None:
        """Create additive private tables without rebuilding an existing DB."""
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            _add_public_run_columns(connection)
            _ensure_schema_version(connection)
            _ensure_private_tables(connection)
            _ensure_indexes(connection)
            _set_schema_version(connection)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def add_work_unit(self, record: ResearchWorkUnitRecord) -> None:
        """Add a digest-addressed work item exactly once."""
        now = _utc_iso(datetime.now(UTC))
        with sqlite_transaction(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO research_work_units (
                    unit_id, run_id, kind, state, input_digest, policy_digest,
                    lease_owner, lease_expires_at, attempt, revision,
                    schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.unit_id,
                    record.run_id,
                    record.kind,
                    record.state,
                    record.input_digest,
                    record.policy_digest,
                    record.lease_owner,
                    (
                        _utc_iso(record.lease_expires_at)
                        if record.lease_expires_at is not None
                        else None
                    ),
                    record.attempt,
                    record.revision,
                    RESEARCH_SCHEMA_VERSION,
                    now,
                    now,
                ),
            )

    def persist_resolution(self, run_id: str, **fields: Any) -> bool:
        """Persist structured private resolution state, never public plaintext.

        Keyword fields are deliberately collected at this boundary so later
        coordinator phases can add optional private projections without
        changing the stable storage method's call shape.
        """
        expected_revision = fields.pop("expected_revision", None)
        status = fields.pop("status", "pending")
        if not isinstance(status, str) or status in TERMINAL_RUN_STATUSES:
            raise ValueError("resolution state must be nonterminal")
        values = _resolution_values(run_id, status, fields)
        with sqlite_transaction(self.db_path) as connection:
            if expected_revision is None:
                cursor = connection.execute(
                    """
                    INSERT INTO research_input_resolutions (
                        run_id, schema_version, status, revision,
                        original_query_digest, original_query_length,
                        effective_query, source_map_json, candidates_json,
                        managed_snapshot_json, effective_query_digest,
                        source_map_digest, candidate_digest, snapshot_digest,
                        evidence_digest, work_digest, client_fingerprint,
                        execution_fingerprint, policy_digest, plan_digest,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO NOTHING
                    """,
                    values,
                )
                return cursor.rowcount == 1
            cursor = connection.execute(
                """
                UPDATE research_input_resolutions
                SET schema_version = ?, status = ?, revision = revision + 1,
                    original_query_digest = ?, original_query_length = ?,
                    effective_query = ?, source_map_json = ?,
                    candidates_json = ?, managed_snapshot_json = ?,
                    effective_query_digest = ?, source_map_digest = ?,
                    candidate_digest = ?, snapshot_digest = ?,
                    evidence_digest = ?, work_digest = ?,
                    client_fingerprint = ?, execution_fingerprint = ?,
                    policy_digest = ?, plan_digest = ?, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    *values[1:19],
                    values[19],
                    run_id,
                    expected_revision,
                ),
            )
            return cursor.rowcount == 1

    def load_resolution(self, run_id: str) -> dict[str, Any] | None:
        """Read private resolution metadata with structured JSON decoded."""
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM research_input_resolutions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in (
            "source_map_json",
            "candidates_json",
            "managed_snapshot_json",
            "inventory_json",
            "final_projection_json",
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
        """CAS-claim a pending or expired retryable item for a live run."""
        now_iso = _utc_iso(now)
        expires_at = _utc_iso(now + ttl)
        connection = sqlite3.connect(self.db_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            record = _work_row(connection, unit_id)
            if record is None or not _claimable(record, now_iso):
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                UPDATE research_work_units
                SET state = 'leased', lease_owner = ?, lease_expires_at = ?,
                    attempt = attempt + 1, updated_at = ?,
                    revision = revision + 1
                WHERE unit_id = ? AND revision = ?
                  AND state = ?
                  AND EXISTS (
                      SELECT 1 FROM runs
                      WHERE runs.run_id = research_work_units.run_id
                        AND runs.status NOT IN (
                            'succeeded', 'failed', 'cancelled'
                        )
                  )
                """,
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
                connection.rollback()
                return None
            claimed = _work_row(connection, unit_id)
            connection.commit()
            return _to_record(claimed) if claimed is not None else None
        finally:
            connection.close()

    def heartbeat_work(
        self,
        unit_id: str,
        lease_owner: str,
        revision: int,
        now: datetime,
    ) -> ResearchWorkUnitRecord | None:
        """Renew only the matching active lease of a nonterminal parent."""
        now_iso = _utc_iso(now)
        expiry = _utc_iso(now + RESEARCH_WORK_LEASE)
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                """
                UPDATE research_work_units
                SET lease_expires_at = ?, updated_at = ?,
                    revision = revision + 1
                WHERE unit_id = ? AND lease_owner = ? AND revision = ?
                  AND state IN ('leased', 'sent')
                  AND EXISTS (
                      SELECT 1 FROM runs
                      WHERE runs.run_id = research_work_units.run_id
                        AND runs.status NOT IN (
                            'succeeded', 'failed', 'cancelled'
                        )
                  )
                """,
                (expiry, now_iso, unit_id, lease_owner, revision),
            )
            if cursor.rowcount != 1:
                return None
            row = _work_row(connection, unit_id)
        return _to_record(row) if row is not None else None

    def complete_work(
        self,
        record: ResearchWorkUnitRecord,
        state: str,
        now: datetime,
    ) -> bool:
        """Set a terminal outcome, rejecting late or superseded completion."""
        if state not in _WORK_STATES - {"pending", "leased", "sent"}:
            raise ValueError(f"unsupported completion state: {state}")
        now_iso = _utc_iso(now)
        with sqlite_transaction(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE research_work_units
                SET state = ?, lease_owner = NULL,
                    lease_expires_at = CASE
                        WHEN ? = 'retryable_failed' THEN lease_expires_at
                        ELSE NULL
                    END,
                    updated_at = ?, completed_at = ?,
                    revision = revision + 1
                WHERE unit_id = ? AND lease_owner = ? AND revision = ?
                  AND state IN ('leased', 'sent')
                  AND EXISTS (
                      SELECT 1 FROM runs
                      WHERE runs.run_id = research_work_units.run_id
                        AND runs.status NOT IN (
                            'succeeded', 'failed', 'cancelled'
                        )
                  )
                """,
                (
                    state,
                    state,
                    now_iso,
                    now_iso,
                    record.unit_id,
                    record.lease_owner,
                    record.revision,
                ),
            )
            return cursor.rowcount == 1

    def purge_run(self, run_id: str) -> None:
        """Delete private children in dependency order, then the parent."""
        with sqlite_transaction(self.db_path) as connection:
            purge_research_children(connection, (run_id,))
            connection.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _add_public_run_columns(connection: sqlite3.Connection) -> None:
    """Extend a legacy ``runs`` table without copying or rewriting its rows."""
    if not _table_exists(connection, "runs"):
        return
    existing = _table_columns(connection, "runs")
    for name, definition in (
        ("stage", "TEXT"),
        ("failure_json", "TEXT"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in existing:
            connection.execute(
                f"ALTER TABLE runs ADD COLUMN {name} {definition}"
            )


def _ensure_schema_version(connection: sqlite3.Connection) -> None:
    """Create and validate the singleton private-schema version row."""
    connection.execute(_CREATE_SCHEMA_VERSION_DDL)
    columns = _table_columns(connection, RESEARCH_SCHEMA_VERSION_TABLE)
    if columns != {"id", "version"}:
        raise sqlite3.DatabaseError(
            "research schema version table is malformed"
        )
    row = connection.execute(
        f"SELECT version FROM {RESEARCH_SCHEMA_VERSION_TABLE} WHERE id = 1"
    ).fetchone()
    if row is not None and int(row[0]) > RESEARCH_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            "research schema is newer than this worker"
        )


def _ensure_private_tables(connection: sqlite3.Connection) -> None:
    """Create current tables and add only missing safe columns."""
    for table, ddl, columns in _PRIVATE_TABLES:
        connection.execute(ddl)
        _add_missing_columns(connection, table, columns)
    connection.execute("""
        UPDATE research_work_units
        SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            updated_at = COALESCE(updated_at, created_at)
        """)


def _add_missing_columns(
    connection: sqlite3.Connection,
    table: str,
    definitions: Sequence[tuple[str, str]],
) -> None:
    """Apply additive columns from introspection inside the migration tx."""
    existing = _table_columns(connection, table)
    for name, definition in definitions:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


def _ensure_indexes(connection: sqlite3.Connection) -> None:
    """Create indexes after all table columns have been migrated."""
    connection.execute("DROP INDEX IF EXISTS uq_research_success_input_policy")
    for statement in _INDEX_DDLS:
        connection.execute(statement)


def _set_schema_version(connection: sqlite3.Connection) -> None:
    """Commit the schema version only after every DDL/index step succeeds."""
    connection.execute(
        f"""
        INSERT INTO {RESEARCH_SCHEMA_VERSION_TABLE}(id, version) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET version = excluded.version
        """,
        (RESEARCH_SCHEMA_VERSION,),
    )


def purge_research_children(
    connection: sqlite3.Connection, run_ids: Sequence[str]
) -> None:
    """Purge grants/outbox/work/resolution/binding before their run owner."""
    ids = tuple(run_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    for table, column in (
        ("research_object_grants", "parent_run_id"),
        ("research_dispatch_outbox", "run_id"),
        ("research_work_units", "run_id"),
        ("research_input_resolutions", "run_id"),
        ("research_idempotency_bindings", "run_id"),
    ):
        if _table_exists(connection, table) and column in _table_columns(
            connection, table
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                ids,
            )


def _claimable(row: sqlite3.Row, now_iso: str) -> bool:
    """Return whether the row can be claimed without stealing a live lease."""
    if row["state"] == "pending":
        return True
    return (
        row["state"] == "retryable_failed"
        and row["lease_expires_at"] is not None
        and row["lease_expires_at"] <= now_iso
    )


def _work_row(
    connection: sqlite3.Connection, unit_id: str
) -> sqlite3.Row | None:
    """Read the small lease projection needed by one compare-and-set."""
    return connection.execute(
        """
        SELECT unit_id, run_id, kind, state, input_digest, policy_digest,
               lease_owner, lease_expires_at, attempt, revision
        FROM research_work_units WHERE unit_id = ?
        """,
        (unit_id,),
    ).fetchone()


def _to_record(row: sqlite3.Row) -> ResearchWorkUnitRecord:
    """Build an immutable record from a SQLite row."""
    return ResearchWorkUnitRecord(
        unit_id=row["unit_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        state=row["state"],
        input_digest=row["input_digest"],
        policy_digest=row["policy_digest"],
        lease_owner=row["lease_owner"],
        lease_expires_at=_parse_iso(row["lease_expires_at"]),
        attempt=row["attempt"],
        revision=row["revision"],
    )


def _utc_iso(value: datetime) -> str:
    """Canonicalize coordinator timestamps to UTC before lexical comparison."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """Convert a storage timestamp back to an aware UTC datetime."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _canonical_json(value: object) -> str:
    """Encode private structured state deterministically and compactly."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _resolution_values(
    run_id: str, status: str, fields: dict[str, Any]
) -> tuple[Any, ...]:
    """Validate and serialize one private resolution mutation."""
    required = _validated_resolution_fields(fields)
    serialized = tuple(_canonical_json(required[index]) for index in (3, 4, 5))
    digests = tuple(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in (required[2], *serialized)
    )
    now = _utc_iso(datetime.now(UTC))
    optional = _optional_resolution_fields(fields)
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
    """Pop and validate the required private resolution values."""
    values = tuple(
        _required_field(fields, name)
        for name in (
            "original_query_digest",
            "original_query_length",
            "effective_query",
            "source_map",
            "parsed_candidates",
            "managed_snapshot",
            "evidence_digest",
            "work_digest",
        )
    )
    if not isinstance(values[0], str):
        raise TypeError("original_query_digest must be text")
    if not isinstance(values[1], int) or isinstance(values[1], bool):
        raise ValueError("original_query_length must be non-negative")
    if values[1] < 0:
        raise ValueError("original_query_length must be non-negative")
    if not isinstance(values[2], str):
        raise TypeError("effective_query must be text")
    if not isinstance(values[3], Mapping):
        raise TypeError("source_map must be a mapping")
    if not isinstance(values[4], Sequence) or isinstance(
        values[4], (str, bytes)
    ):
        raise TypeError("parsed_candidates must be a sequence")
    if not isinstance(values[5], Sequence) or isinstance(
        values[5], (str, bytes)
    ):
        raise TypeError("managed_snapshot must be a sequence")
    if not isinstance(values[6], str) or not isinstance(values[7], str):
        raise TypeError("evidence and work digests must be text")
    return values


def _optional_resolution_fields(fields: dict[str, Any]) -> tuple[Any, ...]:
    """Pop supported optional fields and reject unknown private state."""
    values = tuple(
        fields.pop(name, None)
        for name in (
            "client_fingerprint",
            "execution_fingerprint",
            "policy_digest",
            "plan_digest",
        )
    )
    if fields:
        raise TypeError(f"unexpected resolution fields: {sorted(fields)}")
    return values


def _required_field(fields: dict[str, Any], name: str) -> Any:
    """Pop one required keyword and produce a stable caller error."""
    if name not in fields:
        raise TypeError(f"missing required resolution field: {name}")
    return fields.pop(name)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Return columns for a known local SQLite table."""
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    """Return whether the optional table is present in this database."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


_CREATE_IDEMPOTENCY_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS research_idempotency_bindings (
    run_id TEXT NOT NULL,
    idempotency_digest TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    owner TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT 'research_input_resolution_v1',
    identity_kind TEXT NOT NULL DEFAULT 'header',
    alias_digest TEXT,
    client_fingerprint TEXT,
    conversation_key_digest TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    expires_at TEXT,
    PRIMARY KEY (run_id, idempotency_digest),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""
_CREATE_INPUT_RESOLUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS research_input_resolutions (
    run_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    revision INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    original_query_digest TEXT,
    original_query_length INTEGER NOT NULL DEFAULT 0,
    effective_query TEXT,
    source_map_json TEXT,
    candidates_json TEXT,
    managed_snapshot_json TEXT,
    inventory_json TEXT,
    client_fingerprint TEXT,
    execution_fingerprint TEXT,
    policy_digest TEXT,
    model_id TEXT,
    effective_query_digest TEXT NOT NULL,
    source_map_digest TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    work_digest TEXT NOT NULL,
    coverage_total INTEGER NOT NULL DEFAULT 0,
    coverage_digest TEXT,
    final_projection_json TEXT,
    plan_digest TEXT,
    last_stage TEXT,
    failure_code TEXT,
    failure_retryable INTEGER,
    status_hint TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    expires_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""
_CREATE_WORK_UNITS_DDL = """
CREATE TABLE IF NOT EXISTS research_work_units (
    unit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL DEFAULT 1,
    evidence_ids_json TEXT,
    provider_request_digest TEXT,
    provider_idempotency_digest TEXT,
    output_json TEXT,
    failure_code TEXT,
    failure_retryable INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    sent_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""
_CREATE_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS research_dispatch_outbox (
    outbox_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL DEFAULT 1,
    child_ordinal INTEGER,
    dispatch_fingerprint TEXT,
    payload_json TEXT,
    output_dir TEXT,
    grant_ids_json TEXT,
    snapshot_digest TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    remote_task_id TEXT,
    failure_code TEXT,
    failure_retryable INTEGER,
    updated_at TEXT,
    sent_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (unit_id) REFERENCES research_work_units(unit_id)
)
"""
_CREATE_SCHEMA_VERSION_DDL = f"""
CREATE TABLE IF NOT EXISTS {RESEARCH_SCHEMA_VERSION_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
)
"""
_PRIVATE_TABLES = (
    (
        "research_idempotency_bindings",
        _CREATE_IDEMPOTENCY_BINDINGS_DDL,
        (
            ("run_id", "TEXT"),
            ("idempotency_digest", "TEXT"),
            ("request_digest", "TEXT"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
            ("owner", "TEXT NOT NULL DEFAULT ''"),
            (
                "operation",
                "TEXT NOT NULL DEFAULT 'research_input_resolution_v1'",
            ),
            ("identity_kind", "TEXT NOT NULL DEFAULT 'header'"),
            ("alias_digest", "TEXT"),
            ("client_fingerprint", "TEXT"),
            ("conversation_key_digest", "TEXT"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT"),
            ("expires_at", "TEXT"),
        ),
    ),
    (
        "research_input_resolutions",
        _CREATE_INPUT_RESOLUTIONS_DDL,
        (
            ("run_id", "TEXT"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
            ("status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("revision", "INTEGER NOT NULL DEFAULT 0"),
            ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ("original_query_digest", "TEXT"),
            ("original_query_length", "INTEGER NOT NULL DEFAULT 0"),
            ("effective_query", "TEXT"),
            ("source_map_json", "TEXT"),
            ("candidates_json", "TEXT"),
            ("managed_snapshot_json", "TEXT"),
            ("inventory_json", "TEXT"),
            ("client_fingerprint", "TEXT"),
            ("execution_fingerprint", "TEXT"),
            ("policy_digest", "TEXT"),
            ("model_id", "TEXT"),
            ("effective_query_digest", "TEXT NOT NULL DEFAULT ''"),
            ("source_map_digest", "TEXT NOT NULL DEFAULT ''"),
            ("candidate_digest", "TEXT NOT NULL DEFAULT ''"),
            ("snapshot_digest", "TEXT NOT NULL DEFAULT ''"),
            ("evidence_digest", "TEXT NOT NULL DEFAULT ''"),
            ("work_digest", "TEXT NOT NULL DEFAULT ''"),
            ("coverage_total", "INTEGER NOT NULL DEFAULT 0"),
            ("coverage_digest", "TEXT"),
            ("final_projection_json", "TEXT"),
            ("plan_digest", "TEXT"),
            ("last_stage", "TEXT"),
            ("failure_code", "TEXT"),
            ("failure_retryable", "INTEGER"),
            ("status_hint", "TEXT"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT"),
            ("expires_at", "TEXT"),
        ),
    ),
    (
        "research_work_units",
        _CREATE_WORK_UNITS_DDL,
        (
            ("unit_id", "TEXT"),
            ("run_id", "TEXT"),
            ("kind", "TEXT NOT NULL DEFAULT ''"),
            ("state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("input_digest", "TEXT NOT NULL DEFAULT ''"),
            ("policy_digest", "TEXT NOT NULL DEFAULT ''"),
            ("lease_owner", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("attempt", "INTEGER NOT NULL DEFAULT 0"),
            ("revision", "INTEGER NOT NULL DEFAULT 0"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
            ("evidence_ids_json", "TEXT"),
            ("provider_request_digest", "TEXT"),
            ("provider_idempotency_digest", "TEXT"),
            ("output_json", "TEXT"),
            ("failure_code", "TEXT"),
            ("failure_retryable", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("sent_at", "TEXT"),
            ("completed_at", "TEXT"),
        ),
    ),
    (
        "research_dispatch_outbox",
        _CREATE_OUTBOX_DDL,
        (
            ("outbox_id", "TEXT"),
            ("run_id", "TEXT"),
            ("unit_id", "TEXT"),
            ("payload_digest", "TEXT NOT NULL DEFAULT ''"),
            ("state", "TEXT NOT NULL DEFAULT 'pending'"),
            ("attempt", "INTEGER NOT NULL DEFAULT 0"),
            ("revision", "INTEGER NOT NULL DEFAULT 0"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
            ("child_ordinal", "INTEGER"),
            ("dispatch_fingerprint", "TEXT"),
            ("payload_json", "TEXT"),
            ("output_dir", "TEXT"),
            ("grant_ids_json", "TEXT"),
            ("snapshot_digest", "TEXT"),
            ("lease_owner", "TEXT"),
            ("lease_expires_at", "TEXT"),
            ("remote_task_id", "TEXT"),
            ("failure_code", "TEXT"),
            ("failure_retryable", "INTEGER"),
            ("updated_at", "TEXT"),
            ("sent_at", "TEXT"),
            ("completed_at", "TEXT"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ),
    ),
)
_INDEX_DDLS = (
    "CREATE INDEX IF NOT EXISTS idx_research_binding_digest "
    "ON research_idempotency_bindings(idempotency_digest)",
    "CREATE INDEX IF NOT EXISTS idx_research_work_claim "
    "ON research_work_units(state, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_research_work_run "
    "ON research_work_units(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_research_outbox_pending "
    "ON research_dispatch_outbox(state, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_research_success_input_policy "
    "ON research_work_units(run_id, kind, input_digest, policy_digest) "
    "WHERE state = 'succeeded'",
)
