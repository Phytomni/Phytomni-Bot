# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Local SQLite persistence for explicit user-scoped memory.

This store is deliberately a small single-instance boundary.  It uses one
table, short-lived connections for file-backed databases, WAL, a busy timeout,
parameterized SQL, and explicit transactions around capacity checks plus
writes.  It does not claim that a network filesystem or multiple processes
provide a distributed consistency guarantee; the later API layer decides when
to expose this local store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ...storage.path_policy import IdFactory
from .migrations import MemorySchemaError, ensure_memory_schema
from .models import (
    DEFAULT_MEMORY_POLICY,
    MemoryAuditOperation,
    MemoryAuditRecord,
    MemoryId,
    MemoryKind,
    MemoryPolicy,
    MemoryPolicyError,
    MemoryRecord,
    MemoryWrite,
)

__all__ = [
    "MemoryConflictError",
    "MemoryAuditRecord",
    "MemoryNotFoundError",
    "MemorySchemaError",
    "MemoryStore",
    "MemoryStoreError",
    "memory_audit_context",
]

_CONNECT_TIMEOUT_SECONDS = 10.0
_BUSY_TIMEOUT_MILLISECONDS = 5000
_MEMORY_ID_ADAPTER = TypeAdapter(MemoryId)
_MEMORY_KIND_ADAPTER = TypeAdapter(MemoryKind)
_AUDIT_OPERATION_ADAPTER: TypeAdapter[MemoryAuditOperation] = TypeAdapter(
    MemoryAuditOperation
)
_MAX_AUDIT_LIMIT = 500
_MEMORY_AUDIT_CONTEXT: ContextVar[tuple[str | None, str | None] | None] = (
    ContextVar("phytomni_memory_audit_context", default=None)
)


class MemoryStoreError(RuntimeError):
    """Base error for local memory store operations."""


class MemoryNotFoundError(MemoryStoreError):
    """Raised when an update targets no record in the caller namespace."""


class MemoryConflictError(MemoryStoreError):
    """Raised when an optimistic-concurrency revision is stale."""


@dataclass(frozen=True)
class _MemoryUpdateContext:
    """Validated values needed to atomically replace one memory row."""

    owner: str
    identifier: str
    expected_revision: int
    current: MemoryRecord
    payload: MemoryWrite
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class MemoryListOptions:
    """Optional filters and bounds for one user-scoped list operation."""

    kind: str | None = None
    limit: int | None = None
    now: datetime | None = None
    include_expired: bool = False


@dataclass(frozen=True, slots=True)
class MemoryUpdateOptions:
    """Concurrency fields for replacing one memory record."""

    expected_revision: int
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemoryAuditQuery:
    """Optional filters and pagination for the digest-only audit view."""

    user_id: str | None = None
    operation: str | None = None
    memory_id: str | None = None
    limit: int = 100
    offset: int = 0


def _now_utc(value: datetime | None = None) -> datetime:
    """Return an aware UTC timestamp, rejecting ambiguous naive values."""
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("memory timestamps must include a timezone")
    return timestamp.astimezone(UTC)


def _iso(value: datetime) -> str:
    """Serialize an aware timestamp in the canonical UTC representation."""
    return _now_utc(value).isoformat()


def _decode_tags(raw: str) -> list[str]:
    """Decode the JSON tag list and reject malformed database rows."""
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MemoryStoreError(
            "memory row contains invalid tags JSON"
        ) from exc
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise MemoryStoreError("memory row tags must be a JSON string list")
    return decoded


def _record_digest(record: MemoryRecord) -> str:
    """Return a stable digest without persisting the record's content."""
    canonical = json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def memory_audit_context(
    actor: str | None, request_id: str | None
) -> Generator[None, None, None]:
    """Bind actor/request metadata for one atomic memory mutation."""
    token: Token[tuple[str | None, str | None] | None] = (
        _MEMORY_AUDIT_CONTEXT.set((actor, request_id))
    )
    try:
        yield
    finally:
        _MEMORY_AUDIT_CONTEXT.reset(token)


class MemoryStore:
    """Single-instance local SQLite store for explicit memory records.

    A file-backed operation opens a fresh connection so reads and writes do
    not retain stale transactions.  ``":memory:"`` is supported for focused
    tests by retaining one connection for the lifetime of this object.

    Attributes:
        db_path: Local SQLite path, or ``":memory:"`` for an ephemeral store.
        policy: Per-user limits enforced before every write and read bound.
    """

    def __init__(
        self,
        db_path: str,
        *,
        policy: MemoryPolicy | None = None,
    ) -> None:
        """Initialize the local database and create its indexes.

        Args:
            db_path: Local filesystem path.  Parent directories are created.
            policy: Optional explicit bounds; defaults to the domain policy.
        """
        if not db_path:
            raise ValueError("memory db_path must not be empty")
        self.db_path = str(db_path)
        self.policy = policy or DEFAULT_MEMORY_POLICY
        self._memory_connection: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._memory_connection = sqlite3.connect(
                self.db_path,
                timeout=_CONNECT_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            self._configure_connection(self._memory_connection)
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._init_db()
        except MemorySchemaError:
            raise
        except sqlite3.DatabaseError as exc:
            raise MemorySchemaError("memory database is not usable") from exc

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection) -> None:
        """Apply WAL/busy-timeout settings to one SQLite connection."""
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MILLISECONDS}")
        conn.row_factory = sqlite3.Row

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a configured connection without leaking it on errors."""
        if self._memory_connection is not None:
            yield self._memory_connection
            return
        conn = sqlite3.connect(
            self.db_path,
            timeout=_CONNECT_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            self._configure_connection(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield an IMMEDIATE transaction and roll it back on any error."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            conn.commit()

    def _init_db(self) -> None:
        """Initialize or migrate the memory schema transactionally."""
        with self._connect() as conn:
            ensure_memory_schema(conn)

    @staticmethod
    def _validate_user_id(user_id: str) -> str:
        """Reuse the domain model's namespace validation."""
        return MemoryWrite(
            user_id=user_id,
            kind="context",
            content="validation",
        ).user_id

    @staticmethod
    def _validate_kind(kind: str) -> str:
        """Validate an optional list kind without constructing a record."""
        return _MEMORY_KIND_ADAPTER.validate_python(kind)

    @staticmethod
    def _validate_id(memory_id: str) -> str:
        """Validate an explicit id used by deterministic tests or imports."""
        return _MEMORY_ID_ADAPTER.validate_python(memory_id)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        """Hydrate and revalidate one SQLite row as a domain record."""
        try:
            return MemoryRecord(
                id=row["id"],
                user_id=row["user_id"],
                kind=row["kind"],
                content=row["content"],
                tags=_decode_tags(row["tags_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                expires_at=(
                    datetime.fromisoformat(row["expires_at"])
                    if row["expires_at"] is not None
                    else None
                ),
                revision=row["revision"],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise MemoryStoreError(
                "memory row failed domain validation"
            ) from exc

    @staticmethod
    def _capacity(conn: sqlite3.Connection, user_id: str) -> tuple[int, int]:
        """Return current record count and policy-counted bytes."""
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) "
            "FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1])

    @staticmethod
    def _insert_values(record: MemoryRecord) -> tuple[object, ...]:
        """Build the parameter tuple for an INSERT statement."""
        return (
            record.id,
            record.user_id,
            record.kind,
            record.content,
            json.dumps(record.tags, ensure_ascii=False, separators=(",", ":")),
            _iso(record.created_at),
            _iso(record.updated_at),
            _iso(record.expires_at) if record.expires_at else None,
            record.revision,
            record.size_bytes,
        )

    @staticmethod
    def _audit_row_to_record(row: sqlite3.Row) -> MemoryAuditRecord:
        """Hydrate and validate one digest-only audit row."""
        try:
            return MemoryAuditRecord(
                audit_id=row["audit_id"],
                user_id=row["user_id"],
                actor=row["actor"],
                operation=row["operation"],
                memory_id=row["memory_id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                request_id=row["request_id"],
                before_digest=row["before_digest"],
                after_digest=row["after_digest"],
                revision_before=row["revision_before"],
                revision_after=row["revision_after"],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise MemoryStoreError(
                "memory audit row failed domain validation"
            ) from exc

    @staticmethod
    def _insert_audit(
        conn: sqlite3.Connection,
        *,
        operation: MemoryAuditOperation,
        before: MemoryRecord | None,
        after: MemoryRecord | None,
    ) -> None:
        """Append one digest-only audit row inside the caller transaction."""
        reference = after or before
        if reference is None:
            raise MemoryStoreError("memory audit requires a record")
        bound_context = _MEMORY_AUDIT_CONTEXT.get()
        bound_actor = bound_context[0] if bound_context else None
        request_id = bound_context[1] if bound_context else None
        resolved_actor = (bound_actor or reference.user_id).strip()
        if not resolved_actor:
            raise MemoryStoreError("memory audit actor must not be blank")
        conn.execute(
            "INSERT INTO memory_mutation_audit ("
            "user_id, actor, operation, memory_id, occurred_at, request_id, "
            "before_digest, after_digest, revision_before, revision_after"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reference.user_id,
                resolved_actor,
                operation,
                reference.id,
                _iso(datetime.now(UTC)),
                request_id,
                _record_digest(before) if before else None,
                _record_digest(after) if after else None,
                before.revision if before else None,
                after.revision if after else None,
            ),
        )

    def create(
        self,
        write: MemoryWrite,
        *,
        memory_id: str | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """Create one record after an atomic per-user capacity check."""
        payload = MemoryWrite.model_validate(write)
        self.policy.validate_write(payload)
        timestamp = _now_utc(now)
        record = MemoryRecord(
            id=self._validate_id(
                memory_id or IdFactory().new_id("memory", current=timestamp)
            ),
            user_id=payload.user_id,
            kind=payload.kind,
            content=payload.content,
            tags=payload.tags,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=payload.expires_at,
            revision=1,
        )
        self.policy.validate_record(record)
        with self._transaction() as conn:
            item_count, total_bytes = self._capacity(conn, record.user_id)
            self.policy.ensure_capacity(
                item_count=item_count,
                total_bytes=total_bytes,
                incoming_bytes=record.size_bytes,
            )
            conn.execute(
                "INSERT INTO memories ("
                "id, user_id, kind, content, tags_json, created_at, "
                "updated_at, expires_at, revision, size_bytes"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._insert_values(record),
            )
            self._insert_audit(
                conn,
                operation="create",
                before=None,
                after=record,
            )
        return record

    def get(
        self,
        user_id: str,
        memory_id: str,
        *,
        now: datetime | None = None,
        include_expired: bool = False,
    ) -> MemoryRecord | None:
        """Return one record only when it belongs to ``user_id``."""
        owner = self._validate_user_id(user_id)
        identifier = self._validate_id(memory_id)
        sql = "SELECT * FROM memories WHERE user_id = ? AND id = ?"
        params: list[object] = [owner, identifier]
        if not include_expired:
            sql += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(_iso(_now_utc(now)))
        with self._connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
        return None if row is None else self._row_to_record(row)

    def list(
        self,
        user_id: str,
        *,
        options: MemoryListOptions | None = None,
        **legacy: Any,
    ) -> list[MemoryRecord]:
        """List a user's live records newest-first within the read bound."""
        if options is not None and legacy:
            raise TypeError("options cannot be combined with legacy filters")
        if options is None:
            options = MemoryListOptions(**legacy)
        owner = self._validate_user_id(user_id)
        bounded_limit = self.policy.bounded_retrieval_limit(options.limit)
        clauses = ["user_id = ?"]
        params: list[object] = [owner]
        if options.kind is not None:
            clauses.append("kind = ?")
            params.append(self._validate_kind(options.kind))
        if not options.include_expired:
            clauses.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_iso(_now_utc(options.now)))
        params.append(bounded_limit)
        query = (
            "SELECT * FROM memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id ASC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def export(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        """Export all live records owned by one user namespace.

        Export is intentionally separate from graph retrieval: it uses the
        per-user item bound rather than the smaller prompt-read bound, never
        includes expired rows, and does not accept a caller-supplied owner.
        """
        owner = self._validate_user_id(user_id)
        cutoff = _iso(_now_utc(now))
        query = (
            "SELECT * FROM memories WHERE user_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY updated_at DESC, id ASC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(
                query,
                (owner, cutoff, self.policy.max_items),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _current_for_update(
        conn: sqlite3.Connection,
        owner: str,
        identifier: str,
        expected_revision: int,
    ) -> MemoryRecord:
        """Load a record and enforce its expected revision in one helper."""
        row = conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND id = ?",
            (owner, identifier),
        ).fetchone()
        if row is None:
            raise MemoryNotFoundError("memory record was not found")
        current = MemoryStore._row_to_record(row)
        if current.revision != expected_revision:
            raise MemoryConflictError("memory revision is stale")
        return current

    def _persist_update(
        self,
        conn: sqlite3.Connection,
        context: _MemoryUpdateContext,
    ) -> MemoryRecord:
        """Build, validate, capacity-check, and persist one replacement."""
        current = context.current
        payload = context.payload
        updated = MemoryRecord(
            id=current.id,
            user_id=context.owner,
            kind=payload.kind,
            content=payload.content,
            tags=payload.tags,
            created_at=current.created_at,
            updated_at=context.timestamp,
            expires_at=payload.expires_at,
            revision=current.revision + 1,
        )
        self.policy.validate_record(updated)
        item_count, total_bytes = self._capacity(conn, context.owner)
        self.policy.ensure_capacity(
            item_count=item_count,
            total_bytes=total_bytes,
            incoming_bytes=updated.size_bytes,
            replacing=True,
            existing_bytes=current.size_bytes,
        )
        cursor = conn.execute(
            "UPDATE memories SET kind = ?, content = ?, tags_json = ?, "
            "updated_at = ?, expires_at = ?, revision = ?, size_bytes = ? "
            "WHERE user_id = ? AND id = ? AND revision = ?",
            (
                updated.kind,
                updated.content,
                json.dumps(
                    updated.tags,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                _iso(updated.updated_at),
                _iso(updated.expires_at) if updated.expires_at else None,
                updated.revision,
                updated.size_bytes,
                context.owner,
                context.identifier,
                context.expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise MemoryConflictError("memory revision changed concurrently")
        return updated

    def update(
        self,
        user_id: str,
        memory_id: str,
        write: MemoryWrite,
        *,
        options: MemoryUpdateOptions | None = None,
        **legacy: Any,
    ) -> MemoryRecord:
        """Replace one record when the caller presents its current revision."""
        if options is not None and legacy:
            raise TypeError("options cannot be combined with legacy fields")
        if options is None:
            options = MemoryUpdateOptions(**legacy)
        owner = self._validate_user_id(user_id)
        identifier = self._validate_id(memory_id)
        payload = MemoryWrite.model_validate(write)
        if payload.user_id != owner:
            raise MemoryStoreError(
                "memory update namespace does not match user"
            )
        if options.expected_revision < 1:
            raise MemoryConflictError("memory revision must be positive")
        self.policy.validate_write(payload)
        timestamp = _now_utc(options.now)
        with self._transaction() as conn:
            current = self._current_for_update(
                conn, owner, identifier, options.expected_revision
            )
            updated = self._persist_update(
                conn,
                _MemoryUpdateContext(
                    owner=owner,
                    identifier=identifier,
                    expected_revision=options.expected_revision,
                    current=current,
                    payload=payload,
                    timestamp=timestamp,
                ),
            )
            self._insert_audit(
                conn,
                operation="update",
                before=current,
                after=updated,
            )
        return updated

    def delete(
        self,
        user_id: str,
        memory_id: str,
        *,
        expected_revision: int | None = None,
    ) -> bool:
        """Delete one record idempotently, optionally checking its revision."""
        owner = self._validate_user_id(user_id)
        identifier = self._validate_id(memory_id)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND id = ?",
                (owner, identifier),
            ).fetchone()
            if row is None:
                return False
            current = self._row_to_record(row)
            if (
                expected_revision is not None
                and current.revision != expected_revision
            ):
                raise MemoryConflictError("memory revision is stale")
            cursor = conn.execute(
                "DELETE FROM memories WHERE user_id = ? AND id = ?",
                (owner, identifier),
            )
            if cursor.rowcount == 1:
                self._insert_audit(
                    conn,
                    operation="delete",
                    before=current,
                    after=None,
                )
        return cursor.rowcount == 1

    def list_audit(
        self,
        *,
        query: MemoryAuditQuery | None = None,
        **legacy: Any,
    ) -> Sequence[MemoryAuditRecord]:
        """List digest-only mutation records for a trusted admin caller."""
        if query is not None and legacy:
            raise TypeError("query cannot be combined with legacy filters")
        if query is None:
            query = MemoryAuditQuery(**legacy)
        user_id = query.user_id
        operation = query.operation
        memory_id = query.memory_id
        limit = query.limit
        offset = query.offset
        if limit < 1 or limit > _MAX_AUDIT_LIMIT:
            raise MemoryPolicyError(
                f"audit limit must be between 1 and {_MAX_AUDIT_LIMIT}"
            )
        if offset < 0:
            raise MemoryPolicyError("audit offset must not be negative")
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(self._validate_user_id(user_id))
        if operation is not None:
            clauses.append("operation = ?")
            params.append(_AUDIT_OPERATION_ADAPTER.validate_python(operation))
        if memory_id is not None:
            clauses.append("memory_id = ?")
            params.append(self._validate_id(memory_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((limit, offset))
        statement = (
            "SELECT * FROM memory_mutation_audit"
            + where
            + " ORDER BY occurred_at DESC, audit_id DESC LIMIT ? OFFSET ?"
        )
        with self._connect() as conn:
            rows = conn.execute(statement, tuple(params)).fetchall()
        return [self._audit_row_to_record(row) for row in rows]

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete all expired rows and audit each retention deletion."""
        cutoff = _iso(_now_utc(now))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (cutoff,),
            ).fetchall()
            removed = 0
            for row in rows:
                record = self._row_to_record(row)
                cursor = conn.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (record.id,),
                )
                if cursor.rowcount == 1:
                    self._insert_audit(
                        conn,
                        operation="delete",
                        before=record,
                        after=None,
                    )
                    removed += 1
        return removed

    def close(self) -> None:
        """Close the retained in-memory connection, if one exists."""
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None
