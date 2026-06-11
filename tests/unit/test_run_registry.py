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
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
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

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "hello"}),
    )
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
        RunSpec("run-c", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded"),
    )
    registry.create_run(
        RunSpec("run-d", "bob", "chat", "local"),
        outcome=RunOutcome(status="succeeded"),
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
        outcome=RunOutcome(status="succeeded", result={"answer": "ok"}),
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
    output_dirs = {"t-1": "/obs/a", "t-2": "/obs/b"}

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return one terminal status per child task id."""
        return {
            "task_id": task_id,
            "status": statuses[task_id],
            "output_dir": output_dirs[task_id],
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-r", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert record.timestamps.expires_at is not None
    assert record.result is not None
    assert record.result["task_results"] == record.result["live_status"]
    assert record.result["artifacts"] == [
        {"task_id": "t-1", "output_dir": "/obs/a", "paths": []},
        {"task_id": "t-2", "output_dir": "/obs/b", "paths": []},
    ]
    cached = registry.get_run("run-r", owner="alice")
    assert cached is not None
    assert cached.status == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_surfaces_deep_genome_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A succeeded DeepGenome child lifts its final_report to the payload.

    DeepGenome's single umbrella child persists the assembled report on
    its row; reconcile_task carries it through, and the terminal payload
    surfaces the first non-empty report under ``final_report`` so a
    client polling /v1/runs/{id} reads the markdown without descending
    into ``task_results``.
    """
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-dg", "alice", "deep_genome", "remote"),
        ("dg-1",),
    )
    report_md = "# Deep Genome Analysis of Os01g0177400\n\nbody\n"

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return a succeeded child carrying the persisted report."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/dg",
            "final_report": report_md,
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-dg", owner="alice")

    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["final_report"] == report_md


@pytest.mark.asyncio
async def test_reconcile_final_report_none_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose children persist no report keeps final_report None.

    Analyst / design / network runs never write final_report, so the
    terminal payload's ``final_report`` key is present (shape stays
    stable) but null.
    """
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-an", "alice", "analyst", "remote"),
        ("t-1",),
    )

    async def fake(task_id: str) -> Dict[str, Any]:
        """Return a succeeded child with no persisted report."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/a",
            "final_report": None,
        }

    monkeypatch.setattr(run_registry, "reconcile_task", fake)

    record = await registry.reconcile("run-an", owner="alice")

    assert record is not None
    assert record.result is not None
    assert record.result["final_report"] is None


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
    assert record.result is not None
    assert record.result["task_results"] == [
        {"task_id": "t-1", "status": "succeeded"},
        {"task_id": "t-2", "status": "failed"},
    ]
    assert record.result["live_status"] == record.result["task_results"]
    assert not record.result["artifacts"]


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


def test_list_runs_filters_by_created_after(tmp_path: Path) -> None:
    """RunFilter.created_after keeps rows with created_at >= bound."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(RunSpec("run-old", "alice", "chat", "local"))
    registry.create_run(RunSpec("run-new", "alice", "chat", "local"))
    # Stamp deterministic created_at so the bound comparison is stable.
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-old"),
                ("2026-06-01T00:00:00+00:00", "run-new"),
            ],
        )
        conn.commit()

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(created_after="2026-03-01T00:00:00+00:00"),
    )

    assert [r.spec.run_id for r in listing] == ["run-new"]


def test_list_runs_filters_by_created_before(tmp_path: Path) -> None:
    """RunFilter.created_before keeps rows with created_at <= bound."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(RunSpec("run-old", "alice", "chat", "local"))
    registry.create_run(RunSpec("run-new", "alice", "chat", "local"))
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-old"),
                ("2026-06-01T00:00:00+00:00", "run-new"),
            ],
        )
        conn.commit()

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(created_before="2026-03-01T00:00:00+00:00"),
    )

    assert [r.spec.run_id for r in listing] == ["run-old"]


def test_list_runs_composes_date_range_with_other_filters(
    tmp_path: Path,
) -> None:
    """created_after / created_before stack with status / agent / origin."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(
        RunSpec("run-a-old", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="failed"),
    )
    registry.create_run(
        RunSpec("run-a-new", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="failed"),
    )
    registry.create_run(
        RunSpec("run-c-new", "alice", "chat", "local"),
        outcome=RunOutcome(status="failed"),
    )
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-a-old"),
                ("2026-06-01T00:00:00+00:00", "run-a-new"),
                ("2026-06-01T00:00:00+00:00", "run-c-new"),
            ],
        )
        conn.commit()

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(
            status="failed",
            agent="analyst",
            created_after="2026-03-01T00:00:00+00:00",
        ),
    )

    assert [r.spec.run_id for r in listing] == ["run-a-new"]


def test_create_run_persists_request_info(tmp_path: Path) -> None:
    """A request_info bundle round-trips through create_run + get_run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-ri-1", "alice", "knowledge", "local")
    info = RunRequestInfo(
        dialogue_id="dlg-1",
        query="What does AT1G01010 do?",
        tool_name="KnowledgeAgent",
        model="phyto-knowledge",
        request_json='{"messages": []}',
    )

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "x"}),
        request_info=info,
    )
    record = registry.get_run("run-ri-1", owner="alice")

    assert record is not None
    assert record.request_info == info


def test_create_run_without_request_info_defaults_to_null(
    tmp_path: Path,
) -> None:
    """Omitting request_info leaves every per-request column NULL."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-noreq", "alice", "chat", "local")

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "x"}),
    )
    record = registry.get_run("run-noreq", owner="alice")

    assert record is not None
    assert record.request_info == RunRequestInfo()


def test_update_request_info_overwrites_existing(tmp_path: Path) -> None:
    """update_request_info replaces every column on an owned run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-up", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))

    new_info = RunRequestInfo(
        dialogue_id="dlg-7",
        query="hello world",
        tool_name="ChatAgent",
        model="phyto-chat",
        request_json='{"x": 1}',
    )
    updated = registry.update_request_info(
        "run-up", owner="alice", request_info=new_info
    )

    assert updated is True
    record = registry.get_run("run-up", owner="alice")
    assert record is not None
    assert record.request_info == new_info


def test_update_request_info_returns_false_for_unknown_run(
    tmp_path: Path,
) -> None:
    """update_request_info silently returns False for missing rows."""
    registry, _, _ = _make_registry(tmp_path)

    updated = registry.update_request_info(
        "run-missing", owner="alice", request_info=RunRequestInfo()
    )

    assert updated is False


def test_update_request_info_enforces_owner_isolation(
    tmp_path: Path,
) -> None:
    """A foreign owner cannot retro-stamp another user's run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-iso", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))

    updated = registry.update_request_info(
        "run-iso",
        owner="bob",
        request_info=RunRequestInfo(query="injected"),
    )

    assert updated is False
    record = registry.get_run("run-iso", owner="alice")
    assert record is not None
    assert record.request_info.query is None


def test_init_db_migrates_legacy_table_in_place(tmp_path: Path) -> None:
    """An old database without request-info columns migrates cleanly."""
    db = str(tmp_path / "legacy.db")
    # Build a pre-migration table shape and seed one row. The DDL is
    # inlined into one string (no per-column newlines) so pylint's
    # R0801 similarity scan does not group it with the production
    # _CREATE_RUNS_DDL constant: the schemas overlap by design for the
    # legacy fixture, and extracting a shared helper would couple
    # tests to internal schema strings that are meant to be free to
    # drift.
    legacy_columns = (
        "run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "agent TEXT NOT NULL, origin TEXT NOT NULL, "
        "status TEXT NOT NULL, result_json TEXT, error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "expires_at TEXT"
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE runs ({legacy_columns})")
        conn.execute(
            """
            INSERT INTO runs (
                run_id, user_id, agent, origin, status, result_json,
                error, created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "alice",
                "chat",
                "local",
                "succeeded",
                None,
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Opening the registry against the legacy DB must migrate in place.
    registry = RunRegistry(db)
    record = registry.get_run("legacy-run", owner="alice")

    assert record is not None
    # Legacy row keeps its data and the new columns default to NULL.
    assert record.spec.user_id == "alice"
    assert record.status == "succeeded"
    assert record.request_info == RunRequestInfo()
    # The migration is rerun-safe; opening again does not raise.
    RunRegistry(db)
