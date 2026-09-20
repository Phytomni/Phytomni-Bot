# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite migration contract for the durable execution-event ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.support.execution_contract_fixtures import create_legacy_runs_table

from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def _schema(db_path: Path, kind: str) -> set[str]:
    with sqlite_transaction(db_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
            )
        }


def _columns(db_path: Path, table: str) -> tuple[str, ...]:
    with sqlite_transaction(db_path) as connection:
        return tuple(
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        )


def test_fresh_store_creates_event_and_projection_schema(
    tmp_path: Path,
) -> None:
    """Verify fresh store creates event and projection schema."""

    db_path = tmp_path / "runs.db"
    SQLiteExecutionEventStore(str(db_path))

    assert {"run_events", "run_event_projection"}.issubset(
        _schema(db_path, "table")
    )
    assert {
        "idx_run_events_event_id",
        "idx_run_events_kind_seq",
        "idx_run_events_occurred_at",
    }.issubset(_schema(db_path, "index"))
    assert _columns(db_path, "run_events") == (
        "run_id",
        "seq",
        "event_id",
        "idempotency_key",
        "schema_version",
        "occurred_at",
        "kind",
        "status",
        "task_id",
        "parent_event_id",
        "ignorable",
        "summary_json",
        "public_payload_json",
        "target_json",
        "created_at",
    )
    assert "user_id" not in _columns(db_path, "run_events")


def test_store_migrates_legacy_run_database_without_rewriting_rows(
    tmp_path: Path,
) -> None:
    """Verify store migrates legacy run database without rewriting rows."""

    db_path = tmp_path / "legacy.db"
    with sqlite_transaction(db_path) as connection:
        create_legacy_runs_table(connection)
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
            ("run-legacy", "alice", "chat", "local", "succeeded"),
        )
        connection.commit()

    SQLiteExecutionEventStore(str(db_path))

    with sqlite_transaction(db_path) as connection:
        assert connection.execute(
            "SELECT * FROM runs WHERE run_id = 'run-legacy'"
        ).fetchone() == (
            "run-legacy",
            "alice",
            "chat",
            "local",
            "succeeded",
        )
    assert "run_events" in _schema(db_path, "table")


def test_store_initialization_is_idempotent(tmp_path: Path) -> None:
    """Verify store initialization is idempotent."""

    db_path = tmp_path / "idempotent.db"
    SQLiteExecutionEventStore(str(db_path))
    first = (_schema(db_path, "table"), _schema(db_path, "index"))

    SQLiteExecutionEventStore(str(db_path))

    assert (_schema(db_path, "table"), _schema(db_path, "index")) == first


def test_schema_enforces_sequence_event_and_idempotency_identity(
    tmp_path: Path,
) -> None:
    """Verify schema enforces sequence event and idempotency identity."""

    db_path = tmp_path / "constraints.db"
    SQLiteExecutionEventStore(str(db_path))
    row = (
        "run-1",
        1,
        "evt-1",
        "intent-1",
        1,
        "2026-08-18T00:00:00Z",
        "run.started",
        "running",
        None,
        None,
        0,
        "{}",
        "{}",
        None,
        "2026-08-18T00:00:00Z",
    )
    placeholders = ",".join("?" for _ in row)
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            f"INSERT INTO run_events VALUES ({placeholders})", row
        )
        for duplicate in (
            (*row[:1], 1, "evt-2", "intent-2", *row[4:]),
            (*row[:1], 2, "evt-1", "intent-2", *row[4:]),
            (*row[:1], 2, "evt-2", "intent-1", *row[4:]),
        ):
            try:
                connection.execute(
                    f"INSERT INTO run_events VALUES ({placeholders})",
                    duplicate,
                )
            except sqlite3.IntegrityError:
                continue
            raise AssertionError("duplicate event identity was accepted")
