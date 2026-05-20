# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Migration tests for the run-scoped ``tasks`` table.

Verifies that a fresh database receives every column from the run-scoped
schema, that a legacy 4-column database is upgraded in place by guarded
``ALTER TABLE ADD COLUMN`` calls, and that the public ``get_task`` /
``record_submission`` surface stays byte-equivalent for old callers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.unit

_EXPECTED_COLUMNS = {
    "task_id",
    "status",
    "analysis_id",
    "output_dir",
    "run_id",
    "user_id",
    "agent",
    "origin",
    "created_at",
    "updated_at",
    "input_fingerprint",
}


def _columns(db_path: str) -> set[str]:
    """Return the set of column names on the ``tasks`` table."""
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    finally:
        conn.close()


def test_init_db_creates_full_schema_on_fresh_db(tmp_path: Path) -> None:
    """A new database is created with every run-scoped column."""
    db = str(tmp_path / "tasks.sqlite")

    TaskManager(db)

    assert _columns(db) == _EXPECTED_COLUMNS


def test_init_db_migrates_legacy_four_column_db_in_place(
    tmp_path: Path,
) -> None:
    """A 4-column legacy database is widened in place on next open.

    Pre-existing rows must survive the migration with ``NULL`` in the
    newly added columns (SQLite's documented ADD COLUMN semantics).
    """
    db = str(tmp_path / "tasks.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT,
            analysis_id TEXT,
            output_dir TEXT
        )
        """)
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?)",
        ("legacy-1", "submitted", "", "/legacy/out"),
    )
    conn.commit()
    conn.close()

    TaskManager(db)

    assert _columns(db) == _EXPECTED_COLUMNS
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT status, output_dir, run_id, user_id, agent, origin, "
            "created_at, updated_at FROM tasks WHERE task_id = ?",
            ("legacy-1",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (
        "submitted",
        "/legacy/out",
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Re-initializing an already-migrated database is a no-op."""
    db = str(tmp_path / "tasks.sqlite")

    TaskManager(db)
    TaskManager(db)

    assert _columns(db) == _EXPECTED_COLUMNS


def test_get_task_return_shape_unchanged(tmp_path: Path) -> None:
    """``get_task`` keeps its original 4-key contract after widening.

    The MCP ``GetTaskStatus`` tool reads via this method, so the new
    columns must not leak into its response shape.
    """
    db = str(tmp_path / "tasks.sqlite")
    manager = TaskManager(db)
    manager.record(
        Submission(
            task_id="task-1",
            status="submitted",
            output_dir="/out",
            analysis_id="a-1",
            run_context=RunContext(
                run_id="run-1",
                user_id="alice",
                agent="analyst",
                origin="remote",
                created_at="2026-05-20T00:00:00+00:00",
                updated_at="2026-05-20T00:00:00+00:00",
            ),
        )
    )

    row = manager.get_task("task-1")

    assert row == {
        "task_id": "task-1",
        "status": "submitted",
        "analysis_id": "a-1",
        "output_dir": "/out",
    }


def test_record_submission_without_run_context_writes_null_new_columns(
    tmp_path: Path,
) -> None:
    """Old-style positional calls keep the new columns ``NULL``."""
    db = str(tmp_path / "tasks.sqlite")
    manager = TaskManager(db)

    manager.record_submission("task-old", "submitted", "/out", "a-2")

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT run_id, user_id, agent, origin, created_at, "
            "updated_at FROM tasks WHERE task_id = ?",
            ("task-old",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (None, None, None, None, None, None)


def test_record_submission_with_run_context_writes_all_columns(
    tmp_path: Path,
) -> None:
    """Populated ``RunContext`` flows into every run-scoped column."""
    db = str(tmp_path / "tasks.sqlite")
    manager = TaskManager(db)
    ctx = RunContext(
        run_id="run-2",
        user_id="bob",
        agent="design",
        origin="remote",
        created_at="2026-05-20T01:00:00+00:00",
        updated_at="2026-05-20T01:00:00+00:00",
    )

    manager.record(
        Submission(
            task_id="task-new",
            status="submitted",
            output_dir="/out2",
            analysis_id="a-3",
            run_context=ctx,
        )
    )

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT run_id, user_id, agent, origin, created_at, "
            "updated_at FROM tasks WHERE task_id = ?",
            ("task-new",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (
        "run-2",
        "bob",
        "design",
        "remote",
        "2026-05-20T01:00:00+00:00",
        "2026-05-20T01:00:00+00:00",
    )
