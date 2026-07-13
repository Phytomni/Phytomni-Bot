# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Additive schema initialization and migration for the memory store.

The first schema intentionally has its own version table.  It is not the
LangGraph checkpoint schema and is never inferred from or written into a
checkpoint table.  Existing unversioned C5.2 databases are treated as version
zero and upgraded in place; a future version always fails closed.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

MEMORY_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION_TABLE = "memory_schema_version"

_CREATE_MEMORIES_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    revision INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL
)
"""
_CREATE_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS memory_schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
)
"""
_MEMORY_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "kind",
        "content",
        "tags_json",
        "created_at",
        "updated_at",
        "expires_at",
        "revision",
    }
)


class MemorySchemaError(RuntimeError):
    """Raised when the local memory database cannot be migrated safely."""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return one table's columns, or an empty set when it is absent."""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _size_bytes(content: str, tags_json: str) -> int:
    """Compute the derived byte count while migrating legacy rows."""
    try:
        tags = json.loads(tags_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MemorySchemaError("legacy tags JSON is invalid") from exc
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) for tag in tags
    ):
        raise MemorySchemaError("legacy tags JSON must be a string list")
    return len(content.encode("utf-8")) + sum(
        len(tag.encode("utf-8")) for tag in tags
    )


def _validate_memory_columns(conn: sqlite3.Connection) -> set[str]:
    """Validate required columns and return the complete current set."""
    columns = _table_columns(conn, "memories")
    if not columns:
        conn.execute(_CREATE_MEMORIES_DDL)
        return _table_columns(conn, "memories")
    missing = _MEMORY_REQUIRED_COLUMNS - columns
    if missing:
        names = ", ".join(sorted(missing))
        raise MemorySchemaError(f"required columns missing: {names}")
    return columns


def _migrate_legacy_size_column(
    conn: sqlite3.Connection, columns: Iterable[str]
) -> None:
    """Add and backfill the derived size column for unversioned databases."""
    if "size_bytes" in columns:
        return
    conn.execute(
        "ALTER TABLE memories ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0"
    )
    rows = conn.execute(
        "SELECT id, content, tags_json FROM memories"
    ).fetchall()
    for row in rows:
        if not isinstance(row[1], str) or not isinstance(row[2], str):
            raise MemorySchemaError("legacy memory content is not text")
        conn.execute(
            "UPDATE memories SET size_bytes = ? WHERE id = ?",
            (_size_bytes(row[1], row[2]), row[0]),
        )


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes required by user-scoped reads and expiry purge."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_user_updated "
        "ON memories(user_id, updated_at DESC, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_user_kind_updated "
        "ON memories(user_id, kind, updated_at DESC, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_expires "
        "ON memories(expires_at)"
    )


def _read_version(conn: sqlite3.Connection) -> int | None:
    """Read the singleton version row, rejecting malformed metadata."""
    columns = _table_columns(conn, MEMORY_SCHEMA_VERSION_TABLE)
    if not columns:
        return None
    if columns != {"id", "version"}:
        raise MemorySchemaError("memory schema version table is malformed")
    rows = conn.execute(
        "SELECT version FROM memory_schema_version WHERE id = 1"
    ).fetchall()
    if len(rows) > 1:
        raise MemorySchemaError("memory schema version has duplicate rows")
    if not rows:
        return None
    try:
        return int(rows[0][0])
    except (TypeError, ValueError) as exc:
        raise MemorySchemaError(
            "memory schema version is not an integer"
        ) from exc


def ensure_memory_schema(conn: sqlite3.Connection) -> None:
    """Initialize or migrate one connection to the current schema version."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_CREATE_VERSION_DDL)
        version = _read_version(conn)
        if version is not None and version > MEMORY_SCHEMA_VERSION:
            raise MemorySchemaError(
                "memory database is newer than this application"
            )
        if version is not None and version < 0:
            raise MemorySchemaError("memory schema version is negative")
        columns = _validate_memory_columns(conn)
        if (
            version is None
            or version < MEMORY_SCHEMA_VERSION
            or "size_bytes" not in columns
        ):
            _migrate_legacy_size_column(conn, columns)
            _ensure_indexes(conn)
            conn.execute(
                "INSERT INTO memory_schema_version(id, version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
                (MEMORY_SCHEMA_VERSION,),
            )
        else:
            _ensure_indexes(conn)
        conn.commit()
    except MemorySchemaError:
        conn.rollback()
        raise
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        raise MemorySchemaError("memory database migration failed") from exc


__all__ = [
    "MEMORY_SCHEMA_VERSION",
    "MEMORY_SCHEMA_VERSION_TABLE",
    "MemorySchemaError",
    "ensure_memory_schema",
]
