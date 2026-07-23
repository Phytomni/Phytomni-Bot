# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the local SQLite implementation of explicit memory."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime.memory.models import (
    MemoryPolicy,
    MemoryPolicyError,
    MemoryWrite,
)
from mcp_server_phytomni.runtime.memory.sqlite import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryStore,
    memory_audit_context,
)

pytestmark = pytest.mark.unit


def _now(day: int = 13) -> datetime:
    """Return a deterministic aware timestamp."""
    return datetime(2026, 7, day, 8, 0, tzinfo=UTC)


def _write(**overrides: object) -> MemoryWrite:
    """Build one valid caller-supplied memory payload."""
    values: dict[str, object] = {
        "user_id": "alice",
        "kind": "preference",
        "content": "prefers concise answers",
        "tags": ["style"],
    }
    values.update(overrides)
    return MemoryWrite.model_validate(values)


def _store(tmp_path: Path, policy: MemoryPolicy | None = None) -> MemoryStore:
    """Build a store on a fresh local path."""
    return MemoryStore(str(tmp_path / "memory.sqlite"), policy=policy)


def test_init_creates_schema_and_wal_database(tmp_path: Path) -> None:
    """A new store creates its table, indexes, and local WAL database."""
    store = _store(tmp_path)

    with closed_sqlite_connection(store.db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(memories)")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(memories)")
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {
        "id",
        "user_id",
        "kind",
        "content",
        "tags_json",
        "created_at",
        "updated_at",
        "expires_at",
        "revision",
        "size_bytes",
    } <= columns
    assert any("user" in index for index in indexes)
    assert str(journal_mode).lower() == "wal"


def test_create_and_get_round_trip_is_user_scoped(tmp_path: Path) -> None:
    """Create persists a record and a different user cannot read it."""
    store = _store(tmp_path)
    created = store.create(_write(), memory_id="mem-1", now=_now())

    assert created.id == "mem-1"
    assert created.revision == 1
    assert store.get("alice", "mem-1") == created
    assert store.get("bob", "mem-1") is None


def test_mutations_append_digest_only_audit_records(tmp_path: Path) -> None:
    """Create/update/delete audit rows never persist memory content."""
    store = _store(tmp_path)
    with memory_audit_context("alice", "req-1"):
        created = store.create(
            _write(content="before"), memory_id="mem-1", now=_now(10)
        )
    with memory_audit_context("alice", "req-2"):
        updated = store.update(
            "alice",
            "mem-1",
            _write(content="after"),
            expected_revision=created.revision,
            now=_now(11),
        )
    with memory_audit_context("alice", "req-3"):
        assert store.delete(
            "alice", "mem-1", expected_revision=updated.revision
        )

    audits = store.list_audit(memory_id="mem-1")
    assert [item.operation for item in reversed(audits)] == [
        "create",
        "update",
        "delete",
    ]
    assert all(
        len(item.after_digest or item.before_digest or "") == 64
        for item in audits
    )
    assert all(item.request_id is not None for item in audits)
    with closed_sqlite_connection(store.db_path) as conn:
        raw = conn.execute(
            "SELECT before_digest, after_digest FROM memory_mutation_audit"
        ).fetchall()
    assert all(
        "before" not in value and "after" not in value
        for row in raw
        for value in row
        if value
    )


def test_list_filters_kind_expiry_and_retrieval_bound(tmp_path: Path) -> None:
    """List is newest-first, user-scoped, kind-filtered, and bounded."""
    store = _store(tmp_path)
    store.create(_write(content="old"), memory_id="old", now=_now(10))
    store.create(
        _write(kind="fact", content="new", tags=[]),
        memory_id="new",
        now=_now(11),
    )
    store.create(
        _write(
            content="expires",
            expires_at=_now(12) + timedelta(hours=1),
        ),
        memory_id="expires",
        now=_now(12),
    )

    assert [item.id for item in store.list("alice", now=_now(12))] == [
        "expires",
        "new",
        "old",
    ]
    assert [
        item.id for item in store.list("alice", kind="fact", now=_now(12))
    ] == ["new"]
    assert [
        item.id for item in store.list("alice", limit=1, now=_now(12))
    ] == ["expires"]
    assert store.list("bob") == []

    assert store.get("alice", "expires", now=_now(13)) is None
    assert store.get("alice", "expires", now=_now(13), include_expired=True)


def test_purge_expired_is_audited_and_preserves_live_records(
    tmp_path: Path,
) -> None:
    """TTL deletion is auditable and never removes a live record."""
    store = _store(tmp_path)
    with memory_audit_context("alice", "req-create"):
        store.create(
            _write(
                content="sensitive expired payload",
                expires_at=_now(11),
            ),
            memory_id="expired",
            now=_now(10),
        )
        store.create(
            _write(content="live", expires_at=_now(20)),
            memory_id="live",
            now=_now(10),
        )

    with memory_audit_context("retention", "req-purge"):
        assert store.purge_expired(now=_now(12)) == 1

    assert store.get("alice", "expired", include_expired=True) is None
    live = store.get("alice", "live", now=_now(12))
    assert live is not None
    assert live.content == "live"
    expired_audits = store.list_audit(memory_id="expired")
    assert [item.operation for item in expired_audits] == [
        "delete",
        "create",
    ]
    assert expired_audits[0].actor == "retention"
    with closed_sqlite_connection(store.db_path) as conn:
        raw_audit = conn.execute(
            "SELECT * FROM memory_mutation_audit WHERE memory_id = ?",
            ("expired",),
        ).fetchall()
    assert "sensitive expired payload" not in repr(raw_audit)


def test_export_is_user_scoped_live_only_and_not_retrieval_capped(
    tmp_path: Path,
) -> None:
    """Export returns all live owned rows without exposing another user."""
    policy = MemoryPolicy(
        max_items=3,
        max_content_bytes=100,
        max_total_bytes=500,
        max_retrieval=1,
    )
    store = _store(tmp_path, policy)
    store.create(_write(content="old"), memory_id="old", now=_now(10))
    store.create(_write(content="new"), memory_id="new", now=_now(11))
    store.create(
        _write(content="expired", expires_at=_now(11)),
        memory_id="expired",
        now=_now(10),
    )
    store.create(
        _write(user_id="bob", content="bob"),
        memory_id="bob-memory",
        now=_now(11),
    )

    assert [item.id for item in store.export("alice", now=_now(12))] == [
        "new",
        "old",
    ]
    assert [item.id for item in store.export("bob", now=_now(12))] == [
        "bob-memory"
    ]


def test_update_requires_revision_and_preserves_creation(
    tmp_path: Path,
) -> None:
    """Updates are optimistic-concurrency guarded and increment revision."""
    store = _store(tmp_path)
    created = store.create(_write(), memory_id="mem-1", now=_now(10))

    updated = store.update(
        "alice",
        "mem-1",
        _write(content="updated", tags=["new"]),
        expected_revision=1,
        now=_now(11),
    )

    assert updated.revision == 2
    assert updated.created_at == created.created_at
    assert updated.updated_at == _now(11)
    assert updated.content == "updated"
    with pytest.raises(MemoryConflictError):
        store.update(
            "alice",
            "mem-1",
            _write(content="stale"),
            expected_revision=1,
            now=_now(12),
        )


def test_update_and_get_missing_records_are_explicit(tmp_path: Path) -> None:
    """Update distinguishes a missing record from a stale revision."""
    store = _store(tmp_path)

    with pytest.raises(MemoryNotFoundError):
        store.update(
            "alice",
            "missing",
            _write(),
            expected_revision=1,
            now=_now(),
        )
    assert store.delete("alice", "missing") is False


def test_delete_is_idempotent_and_revision_guarded(tmp_path: Path) -> None:
    """Delete removes only the caller's namespace and may check revision."""
    store = _store(tmp_path)
    store.create(_write(), memory_id="mem-1", now=_now())

    with pytest.raises(MemoryConflictError):
        store.delete("alice", "mem-1", expected_revision=2)
    assert store.delete("bob", "mem-1") is False
    assert store.delete("alice", "mem-1", expected_revision=1) is True
    assert store.delete("alice", "mem-1") is False


def test_capacity_limits_are_enforced_atomically(tmp_path: Path) -> None:
    """Count and byte limits reject a write without a partial row."""
    policy = MemoryPolicy(
        max_items=1,
        max_content_bytes=5,
        max_total_bytes=8,
        max_retrieval=1,
        max_tags=1,
        max_tag_bytes=4,
    )
    store = _store(tmp_path, policy)
    store.create(_write(content="1234", tags=[]), memory_id="one", now=_now())

    with pytest.raises(MemoryPolicyError, match="item limit"):
        store.create(_write(content="x", tags=[]), memory_id="two", now=_now())
    assert [item.id for item in store.list("alice")] == ["one"]

    with pytest.raises(MemoryPolicyError, match="content bytes"):
        store.update(
            "alice",
            "one",
            _write(content="123456", tags=[]),
            expected_revision=1,
            now=_now(11),
        )
    current = store.get("alice", "one")
    assert current is not None
    assert current.content == "1234"


def test_duplicate_id_rolls_back_without_cross_user_overwrite(
    tmp_path: Path,
) -> None:
    """The primary key prevents a caller from replacing another user row."""
    store = _store(tmp_path)
    store.create(_write(), memory_id="mem-1", now=_now())

    with pytest.raises(sqlite3.IntegrityError):
        store.create(
            _write(content="attacker"), memory_id="mem-1", now=_now(11)
        )
    current = store.get("alice", "mem-1")
    assert current is not None
    assert current.content == "prefers concise answers"
