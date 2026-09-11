# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""SQLite owners close their handles when initialization or writes fail."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime import deep_genome_admin
from mcp_server_phytomni.runtime.memory.sqlite import MemoryStore
from mcp_server_phytomni.runtime.task_manager import (
    Submission,
    TaskManager,
    ensure_tasks_table,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("legacy", [False, True])
def test_task_schema_preserves_caller_transaction(legacy: bool) -> None:
    """Task schema initialization leaves commit and handle ownership intact."""
    with closed_sqlite_connection(":memory:") as conn:
        if legacy:
            conn.execute(
                "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT, "
                "analysis_id TEXT, output_dir TEXT)"
            )
            conn.execute(
                "INSERT INTO tasks VALUES ('original', 'running', '', '')"
            )
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        ensure_tasks_table(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert columns >= {"task_log", "final_report", "source_task_id"}
        assert conn.in_transaction
        conn.rollback()
        if legacy:
            restored = conn.execute("PRAGMA table_info(tasks)").fetchall()
            assert len(restored) == 4
            assert conn.execute("SELECT * FROM tasks").fetchall() == [
                ("original", "running", "", "")
            ]
        else:
            assert not conn.execute("PRAGMA table_info(tasks)").fetchall()


@pytest.mark.parametrize("operation", ["create", "update", "record"])
@pytest.mark.parametrize("failure", ["execute", "commit"])
def test_task_write_failure_closes_and_rolls_back(
    tmp_path: Path, operation: str, failure: str
) -> None:
    """SQL and deferred-constraint failures release and roll back writes."""
    manager = TaskManager(str(tmp_path / "tasks.db"))
    task_id = manager.create_task()
    original = manager.get_task(task_id)
    conn = sqlite3.connect(manager.db_path)
    try:
        if failure == "execute":
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute("INSERT INTO child VALUES (1)")
        actions = {
            "create": manager.create_task,
            "update": lambda: manager.update_task(
                task_id, "failed", "analysis", "output"
            ),
            "record": lambda: manager.record(
                Submission(task_id, "failed", "output")
            ),
        }
        with (
            patch.object(manager, "_get_connection", return_value=conn),
            pytest.raises(sqlite3.DatabaseError),
        ):
            actions[operation]()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    finally:
        conn.close()
    assert manager.get_task(task_id) == original
    with closed_sqlite_connection(manager.db_path) as check:
        assert check.execute("SELECT COUNT(*) FROM tasks").fetchone() == (1,)
        if failure == "commit":
            assert check.execute("SELECT COUNT(*) FROM child").fetchone() == (
                0,
            )


def test_task_initialization_failure_closes_connection() -> None:
    """A rejected schema write must not retain the initializing handle."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA query_only=ON")
        with (
            patch("sqlite3.connect", return_value=conn),
            pytest.raises(sqlite3.OperationalError, match="readonly"),
        ):
            TaskManager(":memory:")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_backup_target_open_failure_closes_source(tmp_path: Path) -> None:
    """Opening a backup target can fail before copying begins."""
    source = sqlite3.connect(":memory:")
    try:
        with (
            patch(
                "sqlite3.connect",
                side_effect=[
                    source,
                    sqlite3.OperationalError("target unavailable"),
                ],
            ),
            pytest.raises(sqlite3.OperationalError, match="unavailable"),
        ):
            getattr(deep_genome_admin, "_create_backup")(
                tmp_path / "source.db", tmp_path / "backup.db", 0o600
            )
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            source.execute("SELECT 1")
    finally:
        source.close()


def test_memory_configuration_failure_closes_connection() -> None:
    """A failed in-memory connection setup releases the acquired handle."""
    conn = sqlite3.connect(":memory:")
    try:
        with (
            patch("sqlite3.connect", return_value=conn),
            patch.object(
                MemoryStore,
                "_configure_connection",
                side_effect=sqlite3.DatabaseError("configuration rejected"),
            ),
            pytest.raises(sqlite3.DatabaseError, match="rejected"),
        ):
            MemoryStore(":memory:")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    finally:
        conn.close()
