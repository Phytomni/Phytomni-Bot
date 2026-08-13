# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for worker-thread to asyncio coordination helpers."""

from __future__ import annotations

import asyncio
import gc
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest

from mcp_server_phytomni.runtime import async_utils
from mcp_server_phytomni.runtime.async_utils import wait_for_thread_future

pytestmark = pytest.mark.unit


def _record_wrapped_futures(
    monkeypatch: pytest.MonkeyPatch,
) -> list[asyncio.Future[Any]]:
    """Capture wrappers without changing their completion behavior."""
    wrapped_futures: list[asyncio.Future[Any]] = []
    standard_wrap_future = asyncio.wrap_future

    def record_wrapped_future(future: Any) -> asyncio.Future[Any]:
        """Retain the wrapper until the test deliberately releases it."""
        wrapped = standard_wrap_future(future)
        wrapped_futures.append(wrapped)
        return wrapped

    monkeypatch.setattr(
        async_utils.asyncio,
        "wrap_future",
        record_wrapped_future,
    )
    return wrapped_futures


def _install_stalled_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> asyncio.Future[Any]:
    """Replace the callback bridge with one that never completes."""
    stalled = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(
        async_utils.asyncio,
        "wrap_future",
        lambda _future: stalled,
    )
    return stalled


@pytest.mark.asyncio
async def test_completed_thread_future_returns_when_wrapper_callback_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed source result does not depend on its wrapper callback."""
    future: Future[str] = Future()
    future.set_result("worker-result")
    stalled = _install_stalled_wrapper(monkeypatch)

    async with asyncio.timeout(1):
        result = await wait_for_thread_future(future)

    assert result == "worker-result"
    assert stalled.cancelled()


@pytest.mark.asyncio
async def test_completed_thread_error_raises_when_wrapper_callback_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed source failure crosses a stalled wrapper without logging."""
    provider_marker = "requestId=lost-callback-secret"
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    future: Future[None] = Future()
    future.set_exception(OSError(provider_marker))
    stalled = _install_stalled_wrapper(monkeypatch)

    try:
        with pytest.raises(OSError, match="lost-callback-secret"):
            async with asyncio.timeout(1):
                await wait_for_thread_future(future)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)

    assert stalled.cancelled()
    assert not contexts


@pytest.mark.asyncio
async def test_thread_future_exception_is_retrieved_without_loop_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker failure is awaited without an unretrieved wrapper error."""
    provider_marker = "requestId=secret-request marker=private-page"
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    executor = ThreadPoolExecutor(max_workers=1)
    wrapped_futures = _record_wrapped_futures(monkeypatch)

    def fail() -> None:
        """Raise a marker-bearing worker exception."""
        raise OSError(provider_marker)

    try:
        future = executor.submit(fail)
        assert isinstance(
            future.exception(timeout=5),
            OSError,
        )
        with pytest.raises(OSError, match="secret-request"):
            await wait_for_thread_future(future)
        await asyncio.sleep(0)
        assert len(wrapped_futures) == 1
        wrapped = wrapped_futures[0]
        unretrieved = getattr(wrapped, "_log_traceback", False)
        if not wrapped.cancelled():
            wrapped.exception()
        wrapped_futures.clear()
        gc.collect()
        await asyncio.sleep(0)
    finally:
        executor.shutdown(wait=True)
        loop.set_exception_handler(previous)

    assert not contexts
    assert not unretrieved


@pytest.mark.asyncio
async def test_thread_future_returns_worker_result() -> None:
    """A successful worker result crosses the asyncio bridge unchanged."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(lambda: "worker-result")
        result = await wait_for_thread_future(future)
    finally:
        executor.shutdown(wait=True)

    assert result == "worker-result"


@pytest.mark.asyncio
async def test_thread_future_cancellation_does_not_cancel_running_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation returns promptly and later worker failure is retrieved."""
    provider_marker = "requestId=cancelled-worker-secret"
    release = Event()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    contexts: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    executor = ThreadPoolExecutor(max_workers=1)
    stalled = _install_stalled_wrapper(monkeypatch)

    def fail_after_release() -> None:
        """Keep the worker running until its caller is cancelled."""
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=5)
        raise OSError(provider_marker)

    future = executor.submit(fail_after_release)
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task = asyncio.create_task(wait_for_thread_future(future))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not future.cancelled()
        assert stalled.cancelled()

        release.set()
        assert isinstance(
            future.exception(timeout=5),
            OSError,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
    finally:
        release.set()
        executor.shutdown(wait=True)
        loop.set_exception_handler(previous)

    assert not contexts
