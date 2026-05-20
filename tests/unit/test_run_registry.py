# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the run registry sitting on the shared task DB.

Covers schema migration, sync-run terminal caching with TTL, owner
isolation on get_run/list_runs (404-equivalent for foreign or unknown
ids), filter/paging on list_runs, reconcile's terminal-no-poll guard,
and the manual cascade in purge_expired.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.run_registry import (
    RunFilter,
    RunRecord,
    RunRegistry,
    RunSpec,
    Timestamps,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.unit


def _make_registry(tmp_path: Path) -> tuple[RunRegistry, TaskManager, str]:
    """Return a (registry, manager, db_path) trio on a fresh tmp DB."""
    db = str(tmp_path / "tasks.db")
    return RunRegistry(db), TaskManager(db), db


def _seed_async_run(
    registry: RunRegistry,
    manager: TaskManager,
    spec: RunSpec,
    task_ids: tuple[str, ...],
    task_status: str = "submitted",
) -> None:
    """Insert a running run plus its child task rows."""
    ctx = RunContext(
        run_id=spec.run_id,
        user_id=spec.user_id,
        agent=spec.agent,
        origin=spec.origin,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    for task_id in task_ids:
        manager.record(
            Submission(
                task_id=task_id,
                status=task_status,
                output_dir="/out",
                analysis_id="",
                run_context=ctx,
            )
        )
    registry.create_run(spec)


def test_init_db_creates_runs_table_and_indices(tmp_path: Path) -> None:
    """The registry creates the runs table and shared indices."""
    db = str(tmp_path / "tasks.db")

    RunRegistry(db)

    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indices = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        conn.close()
    assert "runs" in tables
    assert "idx_runs_user" in indices
    assert "idx_tasks_run" in indices


def test_create_run_terminal_caches_result_and_sets_ttl(
    tmp_path: Path,
) -> None:
    """Sync runs are stored terminal with result and an expires_at."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-sync-1", "alice", "chat", "local")

    registry.create_run(spec, status="succeeded", result={"answer": "hello"})
    record = registry.get_run("run-sync-1", owner="alice")

    assert record is not None
    assert record.spec == spec
    assert record.status == "succeeded"
    assert record.result == {"answer": "hello"}
    assert record.error is None
    assert record.timestamps.expires_at is not None
    assert not record.task_ids


def test_get_run_isolates_foreign_owner(tmp_path: Path) -> None:
    """A foreign owner sees the same None response as an unknown id."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-9", "alice", "analyst", "remote"),
        ("t-1",),
    )

    assert registry.get_run("run-9", owner="bob") is None
    assert registry.get_run("run-9", owner="alice") is not None
    assert registry.get_run("missing", owner="alice") is None


def test_list_runs_filters_and_pages(tmp_path: Path) -> None:
    """List honors owner, status/agent/origin filters, and paging."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-a", "alice", "analyst", "remote"),
        ("t-a",),
    )
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-b", "alice", "design", "remote"),
        ("t-b",),
    )
    registry.create_run(
        RunSpec("run-c", "alice", "chat", "local"), status="succeeded"
    )
    registry.create_run(
        RunSpec("run-d", "bob", "chat", "local"), status="succeeded"
    )

    listing = registry.list_runs(owner="alice")
    assert {r.spec.run_id for r in listing} == {"run-a", "run-b", "run-c"}
    by_agent = registry.list_runs(
        owner="alice", run_filter=RunFilter(agent="analyst")
    )
    assert [r.spec.run_id for r in by_agent] == ["run-a"]
    by_origin = registry.list_runs(
        owner="alice", run_filter=RunFilter(origin="local")
    )
    assert [r.spec.run_id for r in by_origin] == ["run-c"]
    paged = registry.list_runs(owner="alice", limit=1, offset=1)
    assert len(paged) == 1


@pytest.mark.asyncio
async def test_reconcile_terminal_run_does_not_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached terminal run is returned without any reconcile_task call."""
    registry, _, _ = _make_registry(tmp_path)
    registry.create_run(
        RunSpec("run-sync-2", "alice", "chat", "local"),
        status="succeeded",
        result={"answer": "ok"},
    )
    calls = {"n": 0}

    async def boom(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Track that the cache guard skips reconcile_task for terminals."""
        _ = (args, kwargs)
        calls["n"] += 1
        return {"status": "succeeded"}

    monkeypatch.setattr(run_registry, "reconcile_task", boom)

    record = await registry.reconcile("run-sync-2", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_reconcile_aggregates_all_succeeded_into_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All success-like child statuses settle the run as succeeded."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-r", "alice", "analyst", "remote"),
        ("t-1", "t-2"),
    )
    statuses = {"t-1": "succeeded", "t-2": "completed"}

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return one terminal status per child task id."""
        return {"task_id": task_id, "status": statuses[task_id]}

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-r", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert record.timestamps.expires_at is not None
    assert record.result is not None
    cached = registry.get_run("run-r", owner="alice")
    assert cached is not None
    assert cached.status == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_propagates_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failed child marks the run failed and records the error."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-f", "alice", "analyst", "remote"),
        ("t-1", "t-2"),
    )
    statuses = {"t-1": "succeeded", "t-2": "failed"}

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return mixed terminal statuses to trigger failure aggregation."""
        return {"task_id": task_id, "status": statuses[task_id]}

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-f", owner="alice")

    assert record is not None
    assert record.status == "failed"
    assert record.error is not None
    assert "t-2" in record.error


def test_purge_expired_cascades_child_tasks(tmp_path: Path) -> None:
    """Expired runs are deleted along with their child task rows."""
    registry, manager, db = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-keep", "alice", "analyst", "remote"),
        ("t-1",),
    )
    registry.create_run(RunSpec("run-stale", "alice", "chat", "local"))
    manager.record(
        Submission(
            task_id="t-stale",
            status="succeeded",
            output_dir="/out",
            run_context=RunContext(run_id="run-stale", user_id="alice"),
        )
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run-stale"),
        )
        conn.commit()
    finally:
        conn.close()

    deleted = registry.purge_expired()

    assert deleted == 1
    assert registry.get_run("run-stale", owner="alice") is None
    assert registry.get_run("run-keep", owner="alice") is not None
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE run_id = ?",
            ("run-stale",),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_run_record_is_frozen_dataclass() -> None:
    """The read view is frozen to enforce immutability at call sites."""
    record = RunRecord(
        spec=RunSpec("r", "u", "a", "local"),
        status="succeeded",
        result=None,
        error=None,
        timestamps=Timestamps(created_at="t", updated_at="t", expires_at=None),
        task_ids=(),
    )

    with pytest.raises(Exception):
        setattr(record, "status", "running")
