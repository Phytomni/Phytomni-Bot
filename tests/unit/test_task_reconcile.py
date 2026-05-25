# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for reconcile_task_log (runtime/task_reconcile.py).

Pin the three reconcile paths that the 2026-05-25 Phase 3 design
locks: cache hit (immediate return), cache miss + remote success
(fetch + write + return), and cache miss + remote failure (return
None, do not propagate the analyst-platform 5xx).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.runtime.task_manager import TaskManager
from mcp_server_phytomni.runtime.task_reconcile import reconcile_task_log

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
