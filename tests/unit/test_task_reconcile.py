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
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.runtime.task_manager import TaskManager
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
