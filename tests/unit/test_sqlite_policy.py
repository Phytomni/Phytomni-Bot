# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared SQLite connection mechanics."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay.audit import (
    RelayAuditQuery,
    RelayAuditStore,
)
from mcp_server_phytomni.api.relay.research_grants import (
    ResearchGrantResolve,
    ResearchGrantStore,
)
from mcp_server_phytomni.runtime import sqlite as sqlite_policy
from mcp_server_phytomni.runtime.sqlite import sqlite_connection
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectSnapshot,
)

pytestmark = pytest.mark.unit


def _private_authority(dataset_id: str) -> ResearchObjectAuthority:
    """Build one observed metadata-port authority for audit isolation."""
    return ResearchObjectAuthority(
        dataset_id=dataset_id,
        authority_id="audit-metadata-authority",
        snapshot=ResearchObjectSnapshot(
            dataset_id=dataset_id,
            size_bytes=31,
            etag="audit-etag",
            version_id="audit-v1",
            last_modified="2026-08-08T00:00:00+00:00",
            placeholder=False,
            snapshot_digest="audit-observed-snapshot",
        ),
    )


def test_sqlite_connection_applies_common_policy(tmp_path: Path) -> None:
    """The helper configures only the mechanics shared by both stores."""
    db_path = tmp_path / "shared.sqlite"

    with sqlite_connection(str(db_path)) as conn:
        assert conn.isolation_level is None
        assert conn.row_factory is None
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")

    with closed_sqlite_connection(db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'marker'"
        ).fetchone() == ("marker",)


def test_closed_sqlite_connection_closes_after_body(
    tmp_path: Path,
) -> None:
    """The test helper commits and closes a plain SQLite connection."""
    connection: sqlite3.Connection | None = None

    with closed_sqlite_connection(str(tmp_path / "closed.sqlite")) as conn:
        connection = conn
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")

    assert connection is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_sqlite_connection_closes_after_body_error(tmp_path: Path) -> None:
    """The helper closes the connection even when the body raises."""
    connection: sqlite3.Connection | None = None

    with (
        pytest.raises(RuntimeError, match="body failed"),
        sqlite_connection(str(tmp_path / "closed.sqlite")) as conn,
    ):
        connection = conn
        raise RuntimeError("body failed")

    assert connection is not None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_sqlite_connection_propagates_locked_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock remains a database error; the helper does not mask it."""
    db_path = tmp_path / "locked.sqlite"
    owner = sqlite3.connect(str(db_path), isolation_level=None)
    owner.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    owner.execute("BEGIN EXCLUSIVE")

    monkeypatch.setattr(sqlite_policy, "_CONNECT_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(sqlite_policy, "_BUSY_TIMEOUT_MILLISECONDS", 0)
    try:
        with (
            pytest.raises(sqlite3.OperationalError, match="locked"),
            sqlite_connection(str(db_path)),
        ):
            pass
    finally:
        monkeypatch.undo()
        owner.rollback()
        owner.close()


def test_sqlite_connection_propagates_unwritable_path(tmp_path: Path) -> None:
    """Filesystem failures are not converted into apparent success."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")

    with (
        pytest.raises(sqlite3.OperationalError, match="unable to open"),
        sqlite_connection(str(parent / "database.sqlite")),
    ):
        pass


def test_sqlite_connection_supports_concurrent_writes(tmp_path: Path) -> None:
    """WAL and the busy timeout preserve concurrent short transactions."""
    db_path = tmp_path / "concurrent.sqlite"
    with sqlite_connection(str(db_path)) as conn:
        conn.execute("CREATE TABLE events (value INTEGER NOT NULL)")

    def write(value: int) -> None:
        with sqlite_connection(str(db_path)) as conn:
            conn.execute("INSERT INTO events(value) VALUES (?)", (value,))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, range(16)))

    with closed_sqlite_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone() == (16,)


def test_sqlite_connection_serializes_fresh_wal_initialization(
    tmp_path: Path,
) -> None:
    """Fresh databases tolerate concurrent first connections."""
    db_path = tmp_path / "fresh-concurrent.sqlite"

    def initialize(value: int) -> None:
        with sqlite_connection(str(db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS markers "
                "(value INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO markers(value) VALUES (?)", (value,))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(initialize, range(16)))

    with closed_sqlite_connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM markers").fetchone() == (16,)


def test_store_schema_migration_and_boundaries_remain_local(
    tmp_path: Path,
) -> None:
    """Auth migration stays separate from the audit schema."""
    auth_db = tmp_path / "api_keys.sqlite"
    with closed_sqlite_connection(auth_db) as conn:
        legacy_columns = (
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "user_id TEXT NOT NULL",
            "name TEXT",
            "key_prefix TEXT NOT NULL",
            "salt TEXT NOT NULL",
            "key_hash TEXT NOT NULL",
            "created_at TEXT NOT NULL",
            "revoked_at TEXT",
            "last_used_at TEXT",
            "expires_at TEXT",
        )
        conn.execute(f"CREATE TABLE api_keys ({', '.join(legacy_columns)})")

    ApiKeyStore(str(auth_db))
    audit_db = tmp_path / "relay_audit.sqlite"
    RelayAuditStore(str(audit_db))

    with closed_sqlite_connection(auth_db) as conn:
        auth_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(api_keys)")
        }
    with closed_sqlite_connection(audit_db) as conn:
        audit_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(relay_audit)")
        }

    assert "scopes" in auth_columns
    assert "scopes" not in audit_columns
    assert "key_hash" not in audit_columns
    assert "request_body" not in auth_columns


def test_relay_audit_queries_never_surface_research_grants(
    tmp_path: Path,
) -> None:
    """Exact references stay in the private grant table, not audit reads."""
    database = tmp_path / "relay.sqlite3"
    exact_reference = "obs://private-bucket/inputs/leaf.tsv"
    candidate = ResearchObjectCandidate(
        dataset_id="audit-dataset",
        exact_reference=exact_reference,
        compound_suffix=".tsv",
    )
    store = ResearchGrantStore(str(database))
    store.resolve_or_replay(
        ResearchGrantResolve(
            principal_key_prefix="ptm_audit",
            parent_run_id="audit-run",
            execution_fingerprint="audit-execution",
            objects=(candidate,),
            authorities=(_private_authority(candidate.dataset_id),),
        ),
        datetime(2026, 8, 8, tzinfo=UTC),
    )

    assert (
        RelayAuditStore(str(database)).query(RelayAuditQuery(limit=100)) == []
    )
    with closed_sqlite_connection(database) as connection:
        grant_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(research_object_grants)"
            )
        }
        audit_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(relay_audit)")
        }
        grant_row = connection.execute(
            "SELECT exact_reference FROM research_object_grants"
        ).fetchone()

    assert grant_row == (exact_reference,)
    assert not any("credential" in column for column in grant_columns)
    assert "exact_reference" not in audit_columns
