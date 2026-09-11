# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Privacy and ownership regressions for cleanup that outlives its caller."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

from mcp_server_phytomni.runtime import cleanup


@pytest.fixture(name="loop_failures")
async def _loop_failures(
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[list[dict[str, object]]]:
    """Record raw loop errors without retaining them on a global logger."""
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    failures: list[dict[str, object]] = []

    def record_failure(
        _loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        failures.append(context)

    loop.set_exception_handler(record_failure)
    try:
        with capture_non_propagating_logger(cleanup.__name__, caplog.handler):
            yield failures
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.parametrize("stop", ["timeout", "cancel", "repeat_cancel"])
async def test_late_cleanup_failure_stays_owned_and_private(
    monkeypatch: pytest.MonkeyPatch,
    loop_failures: list[dict[str, object]],
    caplog: pytest.LogCaptureFixture,
    stop: str,
) -> None:
    """Observe late failure safely after timeout or caller cancellation."""
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    cancellations = 0
    monkeypatch.setattr(cleanup, "CLEANUP_TIMEOUT_SECONDS", 0.01)

    async def fail_after_release() -> None:
        nonlocal cancellations
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations += 1
                cancelled.set()
                continue
        raise RuntimeError("synthetic-cleanup-secret")

    worker = asyncio.create_task(fail_after_release())
    waiter = asyncio.create_task(
        cleanup.run_bounded_cleanup(worker, operation="private_resource")
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        if stop == "timeout":
            assert await waiter is False
        else:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert cleanup.pending_cleanup_count() == 1
        assert not worker.done()

        if stop == "repeat_cancel":
            await asyncio.wait_for(cancelled.wait(), timeout=1.0)
            monkeypatch.setattr(
                cleanup, "CLEANUP_SHUTDOWN_TIMEOUT_SECONDS", 0.01
            )
            with pytest.raises(cleanup.CleanupLifecycleError):
                await cleanup.aclose_cleanup_runtime()
            assert cancellations == 2
            assert cleanup.pending_cleanup_count() == 1

        release.set()
        done, pending = await asyncio.wait({worker}, timeout=1.0)
        assert done == {worker}
        assert not pending
        await asyncio.sleep(0)
        assert cleanup.pending_cleanup_count() == 0
        assert loop_failures == []
        assert "failed after owner transfer exception=RuntimeError" in (
            caplog.text
        )
        assert "synthetic-cleanup-secret" not in caplog.text
        await cleanup.aclose_cleanup_runtime()
    finally:
        release.set()
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(worker, waiter, return_exceptions=True)


async def test_cleanup_cancellation_can_be_a_successful_terminal_result() -> (
    None
):
    """An intentionally cancelled pending iterator can finish cleanup."""
    worker: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    worker.cancel()

    assert await cleanup.run_bounded_cleanup(
        worker, operation="iterator_next", cancelled_is_success=True
    )
    assert cleanup.pending_cleanup_count() == 0
