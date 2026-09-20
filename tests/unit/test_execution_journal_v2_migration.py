# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Additive migration coverage for execution journal V2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.support.execution_contract_fixtures import create_legacy_runs_table

from mcp_server_phytomni.runtime.execution_journal_schema import (
    migrate_execution_journal_v2,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1] for row in connection.execute(f"PRAGMA table_info({table})")
    }


def test_v2_migration_preserves_legacy_runs_tasks_and_v1_events(
    tmp_path: Path,
) -> None:
    """Verify V2 migration preserves legacy runs tasks and V1 events."""

    db_path = tmp_path / "legacy.db"
    with sqlite_transaction(db_path) as connection:
        create_legacy_runs_table(connection)
        connection.execute(
            "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, run_id TEXT)"
        )
        connection.execute(
            "CREATE TABLE run_events (run_id TEXT, seq INTEGER, kind TEXT)"
        )
        connection.execute(
            "INSERT INTO runs VALUES "
            "('run-v1', 'alice', 'chat', 'local', 'succeeded')"
        )
        connection.execute("INSERT INTO tasks VALUES ('task-v1', 'run-v1')")
        connection.execute(
            "INSERT INTO run_events VALUES ('run-v1', 1, 'run.started')"
        )
        migrate_execution_journal_v2(connection)
        connection.commit()

        assert connection.execute("SELECT * FROM runs").fetchall()[0][:5] == (
            "run-v1",
            "alice",
            "chat",
            "local",
            "succeeded",
        )
        assert connection.execute("SELECT * FROM tasks").fetchall() == [
            ("task-v1", "run-v1")
        ]
        assert connection.execute("SELECT * FROM run_events").fetchall() == [
            ("run-v1", 1, "run.started")
        ]

        assert {
            "execution_id",
            "execution_fingerprint_version",
            "execution_fingerprint",
            "execution_driver",
            "execution_deadline_at",
            "execution_terminal_outcome",
            "execution_supervisor_revision",
        }.issubset(_columns(connection, "runs"))
        assert {
            "execution_events_v2",
            "execution_spans",
            "execution_work_units",
            "execution_projection_v2",
            "execution_target_bindings_v2",
        }.issubset(
            {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        )


def test_run_registry_applies_v2_schema_idempotently(tmp_path: Path) -> None:
    """Verify run registry applies V2 schema idempotently."""

    db_path = tmp_path / "registry.db"
    RunRegistry(str(db_path))
    RunRegistry(str(db_path))

    with sqlite_transaction(db_path) as connection:
        assert "execution_id" in _columns(connection, "runs")
        assert _columns(connection, "execution_events_v2") >= {
            "owner_ref",
            "execution_id",
            "seq",
            "event_id",
            "source_idempotency_key",
            "span_id",
            "work_unit_id",
            "attempt",
            "public_payload_json",
        }
        assert _columns(connection, "execution_spans") >= {
            "owner_ref",
            "execution_id",
            "span_id",
            "parent_span_id",
            "status",
            "revision",
        }
        assert _columns(connection, "execution_work_units") >= {
            "owner_ref",
            "execution_id",
            "work_unit_id",
            "join_policy",
            "lease_owner",
            "provider_revision",
            "provider_trace_cursor",
            "provider_trace_revision",
            "provider_trace_adapter_version",
            "provider_trace_overlap_json",
            "provider_trace_contact_at",
            "provider_trace_health",
            "revision",
        }
        assert _columns(connection, "execution_projection_v2") >= {
            "owner_ref",
            "execution_id",
            "latest_seq",
            "projection_json",
        }
        assert _columns(connection, "execution_target_bindings_v2") >= {
            "owner_ref",
            "execution_id",
            "target_kind",
            "target_id",
            "role",
            "name",
            "media_type",
            "size_bytes",
            "delivery_ref",
        }
        assert _columns(connection, "execution_commands_v2") >= {
            "classification",
            "boundary_state",
            "first_error_code",
            "last_error_code",
            "next_reconcile_at",
        }


def test_v2_migration_adds_dispatch_integrity_fields_to_existing_queue(
    tmp_path: Path,
) -> None:
    """Verify V2 migration adds dispatch integrity fields to existing queue."""

    db_path = tmp_path / "legacy-command-queue.db"
    RunRegistry(str(db_path))
    with sqlite_transaction(db_path) as connection:
        connection.execute("DROP TABLE execution_commands_v2")
        connection.execute(
            "CREATE TABLE execution_commands_v2 ("
            "owner_ref TEXT NOT NULL, execution_id TEXT NOT NULL, "
            "command_json TEXT NOT NULL, "
            "state TEXT NOT NULL DEFAULT 'pending', "
            "attempt INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, "
            "lease_owner TEXT, lease_expires_at TEXT, last_error_code TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "revision INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (owner_ref, execution_id))"
        )
        connection.execute(
            "INSERT INTO execution_commands_v2 ("
            "owner_ref, execution_id, command_json, created_at, updated_at) "
            "VALUES ('alice', 'turn-legacy', '{}', 'before', 'before')"
        )
        migrate_execution_journal_v2(connection)
        migrate_execution_journal_v2(connection)
        connection.commit()

        assert _columns(connection, "execution_commands_v2") >= {
            "classification",
            "boundary_state",
            "first_error_code",
            "last_error_code",
            "next_reconcile_at",
        }
        assert connection.execute(
            "SELECT state, attempt, command_json FROM execution_commands_v2 "
            "WHERE execution_id = 'turn-legacy'"
        ).fetchone() == ("pending", 0, "{}")
