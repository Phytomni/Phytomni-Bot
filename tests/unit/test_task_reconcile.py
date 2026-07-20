# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for reconcile_task / reconcile_task_log (runtime/task_reconcile).

Pin the reconcile_task_log three paths (cache hit, cache miss + remote
success, cache miss + remote failure) plus reconcile_task's
final_report passthrough: the column the DeepGenome follow-up node
persists must ride the reconcile dict so the poll formatter and the
run-aggregate can surface the assembled report.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
)
from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeSnapshot,
    DeepGenomeStore,
    snapshot_to_public_dict,
)
from mcp_server_phytomni.runtime.live_tasks import (
    deregister_live_task,
    register_live_task,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)
from mcp_server_phytomni.runtime.task_reconcile import (
    reconcile_task,
    reconcile_task_log,
)

pytestmark = pytest.mark.unit


@pytest.fixture(name="mgr_path")
def _mgr_path(tmp_path: Path) -> str:
    """Return a fresh tasks DB path under ``tmp_path``."""
    return str(tmp_path / "tasks.db")


def _fake_analyst_error() -> McpError:
    """Build an McpError matching the analyst platform's 5xx shape."""
    return McpError(
        ErrorData(code=INTERNAL_ERROR, message="analyst platform 5xx")
    )


async def _raise_probe_failure(_t_id: str, **_: Any) -> dict:
    """Live-probe stub that fails so reconcile uses the local row."""
    raise _fake_analyst_error()


def _install_local_only_reconcile(
    monkeypatch: pytest.MonkeyPatch, mgr_path: str
) -> None:
    """Point reconcile at ``mgr_path`` and fail the live probe."""
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _raise_probe_failure,
    )


def _reserve_deep_genome(
    mgr_path: str,
    *,
    run_id: str = "run-dg",
    task_id: str = "dg-orphan",
) -> tuple[DeepGenomeStore, str]:
    """Reserve a real DeepGenome owner with a durable report snapshot."""
    store = DeepGenomeStore(mgr_path)
    reservation = store.reserve_run(
        run_id=run_id,
        umbrella_task_id=task_id,
        owner="alice",
        output_dir="/obs/run",
    )
    store.apply_brief_gene_transition(
        task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
    )
    store.apply_work_item_transition(
        task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="SMEP summary",
    )
    return store, task_id


def test_snapshot_public_serializer_whitelists_report_fields() -> None:
    """The public DTO hides internal failure keys and arbitrary legacy text."""
    snapshot = DeepGenomeSnapshot(
        umbrella_task_id="task-public",
        status="running",
        intermediate_report="# profile",
        final_report=None,
        report_stage="intermediate",
        report_completeness="partial",
        report_revision=4,
        report_updated_at="2026-07-15T12:00:00+00:00",
        progress={
            "planning_complete": True,
            "brief_gene_status": "succeeded",
            "total": 12,
            "failed": 1,
            "secret": "must not cross boundary",
        },
        degraded=True,
        degraded_reason="secret DSN and legacy response",
        failures=(
            {
                "work_item_key": "smoc_analysis",
                "status": "failed",
                "reason": "secret upstream body",
            },
        ),
    )

    payload = snapshot_to_public_dict(snapshot)

    assert tuple(payload) == DEEP_GENOME_REPORT_FIELDS
    assert tuple(payload["progress"]) == DEEP_GENOME_PROGRESS_FIELDS
    assert payload["report_updated_at"] == "2026-07-15T12:00:00Z"
    assert payload["degraded_reason"] == (
        "analysis results are partially unavailable"
    )
    assert payload["failures"] == [
        {
            "work_item_key": "smoc_analysis",
            "status": "failed",
            "message": "analysis task failed",
        }
    ]


def test_reconcile_task_log_returns_cached_payload_without_remote(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache hit short-circuits before touching the analyst platform.

    The reconcile bridge exists specifically to stop a polling client
    from round-tripping the remote analysis API on every refresh. If a
    cached row exists, the helper must not call the remote primitive —
    pinning that with a monkeypatched counter so a future refactor that
    accidentally re-fetches will fail the assertion immediately.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    task_id = mgr.create_task()
    cached_payload = {
        "init_info": {"goal": "g"},
        "steps": [{"round": 1, "logs": ["cached-output"]}],
    }
    mgr.set_task_log(task_id, cached_payload)

    remote_calls: list[str] = []

    async def _fake_task_log(t_id: str, **_: Any) -> dict:
        remote_calls.append(t_id)
        return {"init_info": {}, "steps": []}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _fake_task_log,
    )

    assert asyncio.run(reconcile_task_log(task_id)) == cached_payload
    assert not remote_calls


def test_reconcile_task_log_fetches_and_caches_on_miss(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache miss + remote success writes the payload and returns it.

    The reconcile bridge must both (a) return the fetched dict on the
    same call and (b) persist it so the next call hits the cache
    instead of re-polling. Both conditions are asserted here.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    task_id = mgr.create_task()

    fetched = {"init_info": {"goal": "live"}, "steps": [{"round": 1}]}

    async def _fake_task_log(_t_id: str, **_: Any) -> dict:
        return fetched

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _fake_task_log,
    )

    result = asyncio.run(reconcile_task_log(task_id))
    assert result == fetched
    assert mgr.get_task_log(task_id) == fetched


def test_reconcile_task_surfaces_persisted_final_report(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reconcile_task carries the row's final_report through the dict.

    The DeepGenome follow-up node persists the assembled markdown on the
    local row; reconcile_task is the single seam feeding both the
    GetTaskStatus formatter and the run-aggregate, so the report must
    ride its return dict. The live ``task_status`` probe is stubbed to
    fail so the call degrades to the locally recorded row.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    mgr.record_submission("dg-1", "succeeded", "/obs/run")
    mgr.set_task_final_report("dg-1", "# Report\n\nbody\n")

    async def _fake_status(_t_id: str, **_: Any) -> dict:
        raise _fake_analyst_error()

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _fake_status,
    )

    result = asyncio.run(reconcile_task("dg-1"))
    assert result["final_report"] == "# Report\n\nbody\n"
    assert result["status"] == "succeeded"


def test_reconcile_task_final_report_none_without_persisted_report(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row with no persisted report carries final_report=None.

    Every non-DeepGenome task leaves the column NULL, so reconcile_task
    must surface None (not crash, not omit the key) so the formatter
    falls back to the status line.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    mgr.record_submission("an-1", "running", "/obs/run")

    async def _fake_status(_t_id: str, **_: Any) -> dict:
        raise _fake_analyst_error()

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _fake_status,
    )

    result = asyncio.run(reconcile_task("an-1"))
    assert result["final_report"] is None


@pytest.mark.parametrize(
    ("recorded_status", "report", "expected"),
    [
        ("running", "# Report\n\nbody\n", "running"),
        ("running", None, "running"),
    ],
)
def test_reconcile_task_self_heal_on_lost_terminal_write(
    mgr_path: str,
    monkeypatch: pytest.MonkeyPatch,
    recorded_status: str,
    report: str | None,
    expected: str,
) -> None:
    """A remote error never promotes a non-terminal legacy row.

    The local report is still returned for display, but status remains the
    durable value until a local owner or snapshot transaction settles it.
    """
    _install_local_only_reconcile(monkeypatch, mgr_path)
    mgr = TaskManager(mgr_path)
    mgr.record_submission("dg-heal", recorded_status, "/obs/run")
    if report is not None:
        mgr.set_task_final_report("dg-heal", report)

    result = asyncio.run(reconcile_task("dg-heal"))

    assert result["status"] == expected


def test_reconcile_task_unknown_id_includes_final_report_key(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown id keeps the final_report key present (value None).

    Shape stability: every reconcile_task return carries the same keys
    so the run-aggregate and formatter never key-check.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    TaskManager(mgr_path)

    result = asyncio.run(reconcile_task("does-not-exist"))
    assert result["status"] == "unknown"
    assert result["final_report"] is None


def test_reconcile_task_log_returns_none_on_remote_failure(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote 5xx must not surface to the caller as an exception.

    A transient analyst-platform outage during a /v1/runs/.../logs
    poll must not translate into a 500 for the user — the endpoint
    returns "no log available yet" so the client can retry. The
    reconcile layer swallows the McpError and returns None.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    task_id = mgr.create_task()

    async def _fake_task_log(_t_id: str, **_: Any) -> dict:
        raise _fake_analyst_error()

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_log",
        _fake_task_log,
    )

    assert asyncio.run(reconcile_task_log(task_id)) is None
    # Nothing was cached — the row still has task_log = NULL.
    assert mgr.get_task_log(task_id) is None


def test_reconcile_task_probes_source_task_id_when_present(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reconcile_task probes the remote id for a caller-owned dedup row.

    A content-addressed dedup hit mints a caller-owned row whose
    source_task_id holds the prior tenant's remote task id. The live
    status probe must ask the analysis platform about the remote id
    (which it knows) rather than the caller's local id (which it never
    minted). A wrong probe returns "unknown" from a valid platform and
    silently mis-reports the run status to the caller.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    mgr.record(
        Submission(
            task_id="T-local",
            status="submitted",
            output_dir="/obs/run",
            source_task_id="R-remote",
        )
    )

    probed_ids: list[str] = []

    async def _capturing_status(t_id: str, **_: Any) -> dict:
        probed_ids.append(t_id)
        return {"status": "running"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _capturing_status,
    )

    asyncio.run(reconcile_task("T-local"))
    assert probed_ids == [
        "R-remote"
    ], f"Expected probe of 'R-remote', got {probed_ids}"


def test_reconcile_task_probes_own_id_when_source_task_id_is_none(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reconcile_task probes its own task_id when source_task_id is None.

    A normal (non-dedup) analyst row has no source_task_id pointer, so
    the live status probe must fall back to the row's own task_id —
    the same id that the analysis platform received at submission time.
    """
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    mgr.record_submission("T-own", "submitted", "/obs/run")

    probed_ids: list[str] = []

    async def _capturing_status(t_id: str, **_: Any) -> dict:
        probed_ids.append(t_id)
        return {"status": "running"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _capturing_status,
    )

    asyncio.run(reconcile_task("T-own"))
    assert probed_ids == [
        "T-own"
    ], f"Expected probe of 'T-own', got {probed_ids}"


def test_reconcile_marks_dead_deep_genome_umbrella_failed(
    mgr_path: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-live deep_genome umbrella with no report reconciles to failed.

    The umbrella's done-callback terminal write was lost (or the process
    restarted), so the row is stuck non-terminal with no final_report and
    the task is gone from the live registry. reconcile must surface it as
    failed rather than leaving a dead run showing running forever, and
    emit a non-secret server-side breadcrumb naming the dead umbrella so
    the restart-orphan / lost-write cause is traceable from the logs.
    """
    _install_local_only_reconcile(monkeypatch, mgr_path)
    store, task_id = _reserve_deep_genome(mgr_path)
    remote_status = AsyncMock()
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        remote_status,
    )

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(reconcile_task(task_id))

    assert result["status"] == "failed"
    assert result["intermediate_report"].startswith("#")
    assert result["final_report"] is None
    assert result["degraded"] is True
    assert result["degraded_reason"] == (
        "workflow interrupted by service restart"
    )
    remote_status.assert_not_awaited()
    snapshot = store.get_snapshot(task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.intermediate_report == result["intermediate_report"]
    with sqlite3.connect(mgr_path) as conn:
        task_row = conn.execute(
            "SELECT status, final_report, intermediate_report, "
            "degraded_reason FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        run_row = conn.execute(
            "SELECT status, error FROM runs WHERE run_id = ?",
            ("run-dg",),
        ).fetchone()
        child_count = conn.execute(
            "SELECT COUNT(*) FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ?",
            (task_id,),
        ).fetchone()[0]
    assert task_row[0] == "failed"
    assert task_row[1] is None
    assert task_row[2].startswith("#")
    assert task_row[3] == "workflow interrupted by service restart"
    assert run_row == ("failed", "workflow interrupted by service restart")
    assert child_count == 12
    assert task_id in caplog.text
    assert "settled failed" in caplog.text


def test_reconcile_skips_remote_probe_for_deep_genome_umbrella(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Umbrella ids are local; jobs/{umbrella} probes only yield 404 noise."""
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    remote_calls: list[str] = []

    async def _track_probe(t_id: str, **_: Any) -> dict:
        remote_calls.append(t_id)
        return {"status": "RUNNING"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _track_probe,
    )
    _install_local_only_reconcile(monkeypatch, mgr_path)
    _, task_id = _reserve_deep_genome(
        mgr_path,
        run_id="run-live",
        task_id="20260604T084205Z-task-deep_genome-a2e59bb1",
    )
    register_live_task(
        task_id,
        cast("asyncio.Task[object]", SimpleNamespace(done=lambda: False)),
    )
    try:
        result = asyncio.run(reconcile_task(task_id))
    finally:
        deregister_live_task(task_id)

    assert not remote_calls
    assert result["status"] == "running"
    assert result["live_status"] is None


def test_reconcile_never_probes_deep_genome_with_source_task_id(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepGenome rows never use remote probes during local reconciliation."""
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.resolve_tasks_db_path",
        lambda: mgr_path,
    )
    mgr = TaskManager(mgr_path)
    mgr.record(
        Submission(
            task_id="dg-dedup-local",
            status="submitted",
            output_dir="/obs/run",
            source_task_id="R-remote",
            run_context=RunContext(agent="deep_genome"),
        )
    )

    probed_ids: list[str] = []

    async def _capturing_status(t_id: str, **_: Any) -> dict:
        probed_ids.append(t_id)
        return {"status": "running"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status",
        _capturing_status,
    )

    asyncio.run(reconcile_task("dg-dedup-local"))
    assert not probed_ids


def test_reconcile_leaves_live_deep_genome_umbrella_running(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-live umbrella stays running (no false-positive failure)."""
    _install_local_only_reconcile(monkeypatch, mgr_path)
    _, task_id = _reserve_deep_genome(
        mgr_path,
        run_id="run-live-2",
        task_id="dg-live",
    )
    register_live_task(
        task_id,
        cast("asyncio.Task[object]", SimpleNamespace(done=lambda: False)),
    )
    try:
        result = asyncio.run(reconcile_task(task_id))
        assert result["status"] == "running"
    finally:
        deregister_live_task(task_id)


def test_reconcile_report_beats_liveness_for_deep_genome(
    mgr_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted final_report wins over the liveness rule -> succeeded."""
    _install_local_only_reconcile(monkeypatch, mgr_path)
    _, task_id = _reserve_deep_genome(
        mgr_path,
        run_id="run-report",
        task_id="dg-rep",
    )
    mgr = TaskManager(mgr_path)
    mgr.set_task_final_report(task_id, "# Report\n\nbody\n")

    result = asyncio.run(reconcile_task(task_id))

    assert result["status"] == "succeeded"


@pytest.mark.parametrize(
    "agent_tag",
    [None, "analyst", "design", "network", "research"],
    ids=["null-child", "analyst", "design", "network", "research"],
)
def test_reconcile_never_fails_remote_row_absent_from_registry(
    mgr_path: str,
    monkeypatch: pytest.MonkeyPatch,
    agent_tag: str | None,
) -> None:
    """A non-deep_genome remote row is never failed by the local rule.

    Non-vacuity guard: a remote analyst sub-task (agent NULL) and the
    other tagged remote agents (analyst / design / network / research)
    are legitimately non-terminal while the platform runs them and are
    never in the local live registry. The liveness rule must fire ONLY
    for agent "deep_genome", so every non-deep_genome row keeps its
    non-terminal status. Covers spec §8 "other remote agent -> unchanged"
    for both the NULL child and the tagged-remote variants; the tagged
    cases pin the guard against a refactor that excludes only NULL rows
    (``agent is None``) and would wrongly fail a tagged remote agent.
    """
    _install_local_only_reconcile(monkeypatch, mgr_path)
    mgr = TaskManager(mgr_path)
    run_context = RunContext(agent=agent_tag) if agent_tag else None
    mgr.record(
        Submission(
            task_id="remote-row",
            status="submitted",
            output_dir="/obs/run",
            run_context=run_context,
        )
    )

    result = asyncio.run(reconcile_task("remote-row"))

    assert result["status"] == "submitted"
