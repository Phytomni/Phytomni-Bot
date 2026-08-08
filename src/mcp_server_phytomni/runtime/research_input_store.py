# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Private, durable SQLite state for bounded Research coordination.

This module intentionally persists identifiers and integrity digests only.  It
does not provide a storage path for uploaded bodies, converted text, or a
second copy of the caller's original query.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .sqlite import sqlite_transaction

RESEARCH_WORK_LEASE = timedelta(seconds=60)
RESEARCH_HEARTBEAT_INTERVAL = timedelta(seconds=20)
RESEARCH_RECOVERY_BATCH_SIZE = 32
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
    lease_expires_at: str | None
    attempt: int
    revision: int


class ResearchInputStore:
    """Own versioned Research coordinator rows colocated with run state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.initialize()

    def initialize(self) -> None:
        """Create additive private tables without rebuilding an existing DB."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            _add_public_run_columns(connection)
            connection.execute(_CREATE_IDEMPOTENCY_BINDINGS_DDL)
            connection.execute(_CREATE_INPUT_RESOLUTIONS_DDL)
            connection.execute(_CREATE_WORK_UNITS_DDL)
            connection.execute(_CREATE_OUTBOX_DDL)
            for statement in _INDEX_DDLS:
                connection.execute(statement)

    def add_work_unit(self, record: ResearchWorkUnitRecord) -> None:
        """Add a pending digest-addressed work item exactly once."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO research_work_units (
                    unit_id, run_id, kind, state, input_digest, policy_digest,
                    lease_owner, lease_expires_at, attempt, revision
                ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, NULL, 0, 0)
                """,
                (
                    record.unit_id,
                    record.run_id,
                    record.kind,
                    record.input_digest,
                    record.policy_digest,
                ),
            )

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
                    attempt = attempt + 1, revision = revision + 1
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
        expiry = _utc_iso(now + RESEARCH_WORK_LEASE)
        with sqlite_transaction(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(
                """
                UPDATE research_work_units
                SET lease_expires_at = ?, revision = revision + 1
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
                (expiry, unit_id, lease_owner, revision),
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
        del now
        if state not in _WORK_STATES - {"pending", "leased", "sent"}:
            raise ValueError(f"unsupported completion state: {state}")
        with sqlite_transaction(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE research_work_units
                SET state = ?, lease_owner = NULL,
                    lease_expires_at = CASE
                        WHEN ? = 'retryable_failed' THEN lease_expires_at
                        ELSE NULL
                    END,
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
                    record.unit_id,
                    record.lease_owner,
                    record.revision,
                ),
            )
            return cursor.rowcount == 1

    def purge_run(self, run_id: str) -> None:
        """Delete private children in dependency order, then the parent."""
        with sqlite_transaction(self.db_path) as connection:
            _purge_research_children(connection, run_id)
            connection.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _add_public_run_columns(connection: sqlite3.Connection) -> None:
    """Extend a legacy ``runs`` table without copying or rewriting its rows."""
    if not _table_exists(connection, "runs"):
        return
    for name, definition in (
        ("stage", "TEXT"),
        ("failure_json", "TEXT"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ):
        with contextlib.suppress(sqlite3.OperationalError):
            connection.execute(
                f"ALTER TABLE runs ADD COLUMN {name} {definition}"
            )


def _purge_research_children(
    connection: sqlite3.Connection, run_id: str
) -> None:
    """Purge grants/outbox/work/resolution/binding before their run owner."""
    for table in (
        "research_input_grants",
        "research_dispatch_outbox",
        "research_work_units",
        "research_input_resolutions",
        "research_idempotency_bindings",
    ):
        if _table_exists(connection, table):
            connection.execute(
                f"DELETE FROM {table} WHERE run_id = ?", (run_id,)
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
        lease_expires_at=row["lease_expires_at"],
        attempt=row["attempt"],
        revision=row["revision"],
    )


def _utc_iso(value: datetime) -> str:
    """Canonicalize coordinator timestamps to UTC before lexical comparison."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, idempotency_digest),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""
_CREATE_INPUT_RESOLUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS research_input_resolutions (
    run_id TEXT PRIMARY KEY,
    effective_query_digest TEXT NOT NULL,
    source_map_digest TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    work_digest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (unit_id) REFERENCES research_work_units(unit_id)
)
"""
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
    "ON research_work_units(run_id, input_digest, policy_digest) "
    "WHERE state = 'succeeded'",
)
