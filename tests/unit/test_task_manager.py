# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the local TaskManager registry.

Pin the Phase 9 registry contract: update_task writes the columns it
names (the prior param-order bug bound ``WHERE task_id = output_dir``
and updated zero rows), get_task is a single non-blocking read, and
record_submission upserts a caller-known task_id.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.task_manager import (
    TaskManager,
    resolve_tasks_db_path,
)

pytestmark = pytest.mark.unit


def _mgr(tmp_path: Path) -> TaskManager:
    """Return a TaskManager backed by an isolated temp database.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        A TaskManager whose SQLite file lives under ``tmp_path``.
    """
    return TaskManager(db_path=str(tmp_path / "tasks.db"))


def test_create_update_get_roundtrip(tmp_path: Path) -> None:
    """Verify create -> update -> get returns the written columns.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the round-trip row assertion passes.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    mgr.update_task(task_id, "succeeded", "remote-7", "/obs/out")

    assert mgr.get_task(task_id) == {
        "task_id": task_id,
        "status": "succeeded",
        "analysis_id": "remote-7",
        "output_dir": "/obs/out",
    }


def test_update_task_writes_named_columns(tmp_path: Path) -> None:
    """Regression: update_task no longer scrambles its parameters.

    The prior tuple ``(status, task_id, analysis_id, output_dir)``
    bound ``WHERE task_id = output_dir`` and wrote ``analysis_id`` into
    the task_id slot, so a task row never updated.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the per-column assertions pass.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    mgr.update_task(task_id, "running", "A1", "/d")

    row = mgr.get_task(task_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["analysis_id"] == "A1"
    assert row["output_dir"] == "/d"


def test_get_task_missing_returns_none(tmp_path: Path) -> None:
    """Verify an unknown task id reads back as None.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the missing-id assertion passes.
    """
    assert _mgr(tmp_path).get_task("does-not-exist") is None


def test_record_submission_upserts_known_id(tmp_path: Path) -> None:
    """Verify record_submission writes then replaces a known id.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the insert and replace assertions pass.
    """
    mgr = _mgr(tmp_path)

    mgr.record_submission("local-42", "submitted", "/obs/run")
    assert mgr.get_task("local-42") == {
        "task_id": "local-42",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/run",
    }

    mgr.record_submission("local-42", "succeeded", "/obs/run", "rem-9")
    row = mgr.get_task("local-42")
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["analysis_id"] == "rem-9"


def test_resolve_tasks_db_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify the resolver reads the ApiConfig tasks-db path alias.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the override path assertion passes.
    """
    override = str(tmp_path / "custom_tasks.db")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", override)

    assert resolve_tasks_db_path() == override
