# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""``degraded_reason`` column round trip + legacy-DB migration."""

from __future__ import annotations

import sqlite3

import pytest

from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.unit


def test_set_and_get_degraded_reason(tmp_path) -> None:
    """A persisted reason reads back; an untouched row reads None."""
    db = str(tmp_path / "tasks.db")
    manager = TaskManager(db)
    task_id = manager.create_task()

    assert manager.get_task_degraded(task_id) is None

    assert manager.set_task_degraded(task_id, "gene overview unavailable")
    assert manager.get_task_degraded(task_id) == "gene overview unavailable"


def test_set_degraded_unknown_task_returns_false(tmp_path) -> None:
    """Setting on an unknown id updates no row."""
    manager = TaskManager(str(tmp_path / "tasks.db"))
    assert manager.set_task_degraded("nope", "reason") is False


def test_legacy_db_gains_degraded_column(tmp_path) -> None:
    """A pre-column 4-column DB is widened in place on open."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, status TEXT, "
        "analysis_id TEXT, output_dir TEXT)"
    )
    conn.execute("INSERT INTO tasks VALUES ('t1', 'running', 'a', 'o')")
    conn.commit()
    conn.close()

    manager = TaskManager(db)  # _init_db runs the guarded ALTERs

    assert manager.get_task_degraded("t1") is None
    assert manager.set_task_degraded("t1", "deg")
    assert manager.get_task_degraded("t1") == "deg"
