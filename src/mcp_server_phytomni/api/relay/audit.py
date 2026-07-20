# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite-backed audit store for the credential-injecting relay.

Models: RelayAuditRecord, RelayAuditQuery. Class: RelayAuditStore.
Function: get_audit_store.

Persists relay request/response metadata and redacted bodies by request
id. No key hash, salt, or plaintext key is stored, only the public
prefix; the DB stays local because SQLite WAL deadlocks on network FS.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from ...runtime.sqlite import sqlite_connection
from .audit_filter import redact_body_text

__all__ = [
    "RelayAuditRecord",
    "RelayAuditQuery",
    "RelayAuditStore",
    "get_audit_store",
]


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


class RelayAuditRecord(BaseModel):
    """One relay audit row, used as both write payload and read view.

    On write, ``id`` and ``created_at`` are left unset and assigned by
    the store; on read every field is populated from the database row.

    Attributes:
        id: Autoincrement row id (None before insert).
        request_id: Correlation id for the relayed request.
        user_id: Authenticated caller bound to the API key.
        key_prefix: Public prefix of the presented key, for audit.
        service: Relay service name (e.g. ``llm``, ``retrieve``).
        operation: Optional sub-operation label.
        status_code: Upstream HTTP status, or None on transport error.
        duration_ms: Wall-clock forwarding latency in milliseconds.
        request_body: Redacted request body, capped by the forwarding path.
        response_body: Redacted response body, capped by the forwarding path.
        error_type: Exception type name when forwarding failed.
        created_at: ISO-8601 creation timestamp (None before insert).
    """

    id: int | None = None
    request_id: str
    user_id: str
    key_prefix: str
    service: str
    operation: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    request_body: str | None = None
    response_body: str | None = None
    error_type: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RelayAuditRecord:
        """Build a record from a sqlite3.Row with named columns.

        Args:
            row: A row from the ``relay_audit`` table.

        Returns:
            The typed record view of that row.
        """
        return cls(
            id=row["id"],
            request_id=row["request_id"],
            user_id=row["user_id"],
            key_prefix=row["key_prefix"],
            service=row["service"],
            operation=row["operation"],
            status_code=row["status_code"],
            duration_ms=row["duration_ms"],
            request_body=redact_body_text(row["request_body"]),
            response_body=redact_body_text(row["response_body"]),
            error_type=row["error_type"],
            created_at=row["created_at"],
        )


class RelayAuditQuery(BaseModel):
    """Optional filters for listing relay audit records.

    Attributes:
        user_id: Filter to a single bound user.
        key_prefix: Filter to a single key prefix.
        service: Filter to a single relay service.
        status_code: Filter to a single upstream status code.
        created_after: Inclusive ISO-8601 lower time bound.
        created_before: Inclusive ISO-8601 upper time bound.
        limit: Maximum rows to return.
        offset: Rows to skip for pagination.
    """

    user_id: str | None = None
    key_prefix: str | None = None
    service: str | None = None
    status_code: int | None = None
    created_after: str | None = None
    created_before: str | None = None
    limit: int = 100
    offset: int = 0


class RelayAuditStore:
    """SQLite store for relay request/response audit records.

    The database must stay on a local filesystem; network filesystems
    deadlock under SQLite WAL. No API-key hash, salt, or plaintext key is
    ever stored here, only the public ``key_prefix`` for correlation.

    Attributes:
        db_path: Filesystem path to the SQLite database.
    """

    def __init__(self, db_path: str) -> None:
        """Initialize the store, creating the schema if needed.

        Args:
            db_path: SQLite database path for the audit store.
        """
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relay_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    service TEXT NOT NULL,
                    operation TEXT,
                    status_code INTEGER,
                    duration_ms INTEGER,
                    request_body TEXT,
                    response_body TEXT,
                    error_type TEXT,
                    created_at TEXT NOT NULL
                )
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_audit_created "
                "ON relay_audit(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_audit_request "
                "ON relay_audit(request_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_audit_user "
                "ON relay_audit(user_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_audit_keyprefix "
                "ON relay_audit(key_prefix, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relay_audit_service "
                "ON relay_audit(service, created_at)"
            )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a short-lived autocommit WAL connection with Row access.

        The shared helper owns connection lifecycle and PRAGMAs; this wrapper
        keeps the audit store's named-row projection local to its schema.
        """
        with sqlite_connection(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    def record(self, entry: RelayAuditRecord) -> int:
        """Persist one audit record atomically and return its row id.

        Args:
            entry: The audit row to persist. ``id`` is ignored and
                ``created_at`` defaults to now when unset.

        Returns:
            The autoincrement id of the inserted row.
        """
        created_at = entry.created_at or _now_iso()
        request_body = redact_body_text(entry.request_body)
        response_body = redact_body_text(entry.response_body)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO relay_audit (
                    request_id, user_id, key_prefix, service, operation,
                    status_code, duration_ms, request_body, response_body,
                    error_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.request_id,
                    entry.user_id,
                    entry.key_prefix,
                    entry.service,
                    entry.operation,
                    entry.status_code,
                    entry.duration_ms,
                    request_body,
                    response_body,
                    entry.error_type,
                    created_at,
                ),
            )
        return int(cursor.lastrowid or 0)

    def query(
        self, criteria: RelayAuditQuery | None = None
    ) -> list[RelayAuditRecord]:
        """List audit records matching optional filters, newest first.

        Args:
            criteria: Optional filters; when None, every record is
                returned subject to the default limit and offset.

        Returns:
            Matching records ordered by descending row id.
        """
        criteria = criteria or RelayAuditQuery()
        clauses: list[str] = []
        params: list[object] = []
        if criteria.user_id is not None:
            clauses.append("user_id = ?")
            params.append(criteria.user_id)
        if criteria.key_prefix is not None:
            clauses.append("key_prefix = ?")
            params.append(criteria.key_prefix)
        if criteria.service is not None:
            clauses.append("service = ?")
            params.append(criteria.service)
        if criteria.status_code is not None:
            clauses.append("status_code = ?")
            params.append(criteria.status_code)
        if criteria.created_after is not None:
            clauses.append("created_at >= ?")
            params.append(criteria.created_after)
        if criteria.created_before is not None:
            clauses.append("created_at <= ?")
            params.append(criteria.created_before)
        sql = "SELECT * FROM relay_audit"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([criteria.limit, criteria.offset])
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [RelayAuditRecord.from_row(row) for row in rows]

    def get_by_request_id(self, request_id: str) -> list[RelayAuditRecord]:
        """Return all audit records sharing a request id, newest first.

        Args:
            request_id: The correlation id to look up.

        Returns:
            Matching records ordered by descending row id.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM relay_audit WHERE request_id = ? "
                "ORDER BY id DESC",
                (request_id,),
            ).fetchall()
        return [RelayAuditRecord.from_row(row) for row in rows]

    def purge_expired(self, retention_days: int) -> int:
        """Delete records older than the retention window.

        Args:
            retention_days: Maximum record age in days.

        Returns:
            The number of deleted rows.
        """
        cutoff = (
            datetime.now(UTC) - timedelta(days=retention_days)
        ).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM relay_audit WHERE created_at < ?", (cutoff,)
            )
        return cursor.rowcount


@cache
def get_audit_store(db_path: str) -> RelayAuditStore:
    """Return a process-cached audit store for a resolved database path.

    Args:
        db_path: SQLite database path for the audit store.

    Returns:
        A shared ``RelayAuditStore`` so the schema is initialized once.
    """
    return RelayAuditStore(str(Path(db_path).resolve()))
