# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for additive, idempotent memory schema migrations."""

import json
import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.memory.sqlite import (
    MemorySchemaError,
    MemoryStore,
)

pytestmark = pytest.mark.unit


def _legacy_db(path: Path, *, with_size: bool = False) -> None:
    """Create a pre-version-table memory schema fixture."""
    columns = """
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        revision INTEGER NOT NULL
    """
    if with_size:
        columns += ", size_bytes INTEGER NOT NULL"
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE memories ({columns})")
        values: tuple[object, ...] = (
            "mem-1",
            "alice",
            "fact",
            "Arabidopsis",
            json.dumps(["plant"]),
            "2026-07-13T08:00:00+00:00",
            "2026-07-13T08:00:00+00:00",
            None,
            1,
        )
        if with_size:
            values += (0,)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO memories VALUES ({placeholders})", values)


def test_empty_database_gets_version_table_and_indexes(tmp_path: Path) -> None:
    """A fresh database is initialized at the current schema version."""
    store = MemoryStore(str(tmp_path / "memory.sqlite"))

    with sqlite3.connect(store.db_path) as conn:
        version = conn.execute(
            "SELECT version FROM memory_schema_version WHERE id = 1"
        ).fetchone()
        memory_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(memories)")
        }

    assert version == (1,)
    assert "idx_memories_user_updated" in memory_indexes


def test_existing_unversioned_schema_is_migrated_and_repeatable(
    tmp_path: Path,
) -> None:
    """A C5.2-style database is upgraded without losing rows."""
    path = tmp_path / "memory.sqlite"
    _legacy_db(path)

    first = MemoryStore(str(path))
    second = MemoryStore(str(path))

    record = second.get("alice", "mem-1")
    assert record is not None
    assert record.size_bytes == len(b"Arabidopsis") + len("plant")
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_schema_version"
        ).fetchone() == (1,)
    first.close()


def test_migration_backfills_missing_size_column(tmp_path: Path) -> None:
    """The additive derived-byte column is added and populated once."""
    path = tmp_path / "memory.sqlite"
    _legacy_db(path)

    MemoryStore(str(path))

    with sqlite3.connect(path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(memories)")
        }
        size = conn.execute(
            "SELECT size_bytes FROM memories WHERE id = 'mem-1'"
        ).fetchone()
    assert "size_bytes" in columns
    assert size == (len(b"Arabidopsis") + len("plant"),)


def test_unknown_schema_version_fails_closed(tmp_path: Path) -> None:
    """A newer schema is never silently opened by an older binary."""
    path = tmp_path / "memory.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE memory_schema_version ("
            "id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO memory_schema_version(id, version) VALUES (1, 99)"
        )

    with pytest.raises(MemorySchemaError, match="newer"):
        MemoryStore(str(path))


def test_corrupt_database_fails_with_schema_error(tmp_path: Path) -> None:
    """A non-SQLite file is reported as a schema error, not recreated."""
    path = tmp_path / "memory.sqlite"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(MemorySchemaError, match="database"):
        MemoryStore(str(path))


def test_required_columns_are_not_invented_for_incompatible_legacy_db(
    tmp_path: Path,
) -> None:
    """An incompatible table fails closed instead of destructive guessing."""
    path = tmp_path / "memory.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY)")

    with pytest.raises(MemorySchemaError, match="required columns"):
        MemoryStore(str(path))


def test_checkpoint_table_is_not_reused_or_modified(tmp_path: Path) -> None:
    """Memory migrations keep LangGraph checkpoint lifecycle independent."""
    path = tmp_path / "memory.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO checkpoints(thread_id) VALUES ('thread-1')")

    MemoryStore(str(path))

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM checkpoints").fetchall() == [
            ("thread-1",)
        ]
