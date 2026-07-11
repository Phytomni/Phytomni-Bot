# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""The run-registry GC runs as a BackgroundTask on the write paths.

Pins that the three sync write routes declare _schedule_run_gc rather
than blocking the response on the SQLite DELETE scan. The listing
route's inline purge and the SSE-finally purge stay out of scope.
"""

# pylint: disable=protected-access
# Test file exercises ``_schedule_run_gc`` (a module-private dependency
# function) directly to assert the three write routes declare it.

from __future__ import annotations

import asyncio
import inspect
import threading

import pytest
from fastapi import BackgroundTasks

from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def test_sync_write_routes_declare_gc_dependency() -> None:
    """The three sync write routes carry the _schedule_run_gc dependency."""
    app = api_app.create_app()
    wanted = {
        "/v1/agents/{agent}/runs",
        "/v1/query/route",
        "/v1/chat/completions",
    }
    seen: dict[str, bool] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in wanted:
            deps = getattr(route, "dependencies", [])
            dep_calls = [d.dependency for d in deps]
            seen[path] = api_app._schedule_run_gc in dep_calls
    assert seen == {p: True for p in wanted}


async def test_gc_dependency_and_background_task_are_native_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run GC without Starlette's worker-thread completion bridge."""
    calls = 0

    def _purge_spy() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        api_app,
        "_purge_expired_runs_best_effort",
        _purge_spy,
    )
    assert inspect.iscoroutinefunction(api_app._schedule_run_gc)
    background = BackgroundTasks()

    await api_app._schedule_run_gc(background)

    assert len(background.tasks) == 1
    assert (
        background.tasks[0].func
        is api_app._purge_expired_runs_best_effort_async
    )
    assert inspect.iscoroutinefunction(background.tasks[0].func)
    await background()
    assert calls == 1


async def test_async_gc_keeps_the_request_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-response SQLite pass stays off the request event loop."""
    started = threading.Event()
    release = threading.Event()

    def _blocking_purge() -> None:
        started.set()
        release.wait(timeout=0.2)

    monkeypatch.setattr(
        api_app,
        "_purge_expired_runs_best_effort",
        _blocking_purge,
    )
    task = asyncio.create_task(api_app._purge_expired_runs_best_effort_async())
    for _ in range(1000):
        if started.is_set():
            break
        await asyncio.sleep(0)

    assert started.is_set()
    assert not task.done()
    release.set()
    await task


async def test_async_gc_coalesces_concurrent_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent write responses share one process-local GC pass."""
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def _blocking_purge() -> None:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=0.2)

    monkeypatch.setattr(
        api_app,
        "_purge_expired_runs_best_effort",
        _blocking_purge,
    )
    first = asyncio.create_task(
        api_app._purge_expired_runs_best_effort_async()
    )
    for _ in range(1000):
        if started.is_set():
            break
        await asyncio.sleep(0)
    second = asyncio.create_task(
        api_app._purge_expired_runs_best_effort_async()
    )
    for _ in range(1000):
        if calls > 1 or second.done():
            break
        await asyncio.sleep(0)
    observed_calls = calls
    release.set()
    await asyncio.gather(first, second)

    assert observed_calls == 1


async def test_async_gc_reraises_unexpected_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected worker failures remain visible to Starlette logging."""

    def _boom() -> None:
        raise RuntimeError("gc worker failed")

    monkeypatch.setattr(
        api_app,
        "_purge_expired_runs_best_effort",
        _boom,
    )

    with pytest.raises(RuntimeError, match="gc worker failed"):
        await api_app._purge_expired_runs_best_effort_async()
