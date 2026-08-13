# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small asyncio coordination helpers for runtime worker threads."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import Any


def _retrieve_future_result(
    future: asyncio.Future[Any] | Future[Any],
) -> None:
    """Retrieve a completed future without propagating its result."""
    if not future.cancelled():
        future.exception()


def _observe_cancelled_thread_wait(
    future: Future[Any],
    wrapped: asyncio.Future[Any],
) -> None:
    """Detach a cancelled waiter while observing both completion sources."""
    if not wrapped.done():
        wrapped.cancel()
    if wrapped.done():
        _retrieve_future_result(wrapped)
    if future.done():
        _retrieve_future_result(future)
    else:
        future.add_done_callback(_retrieve_future_result)


def _completed_thread_future_result(
    future: Future[Any],
    wrapped: asyncio.Future[Any],
) -> Any:
    """Return a source result without depending on its wrapper callback."""
    if wrapped.done():
        return wrapped.result()
    if not wrapped.cancel():
        _retrieve_future_result(wrapped)
    return future.result()


async def wait_for_thread_event(event: asyncio.Event) -> None:
    """Wait for a worker-thread event while yielding to the event loop.

    Some supported asyncio runners do not wake a direct ``Event.wait`` after
    ``call_soon_threadsafe`` from a daemon worker. A bounded retry preserves
    cancellation and keeps the loop responsive while the callback is queued.
    """
    while not event.is_set():
        try:
            await asyncio.wait_for(event.wait(), timeout=0.01)
        except TimeoutError:
            continue


async def wait_for_thread_future(future: Future[Any]) -> Any:
    """Wait for a worker-thread future without relying on wakeup callbacks.

    A few supported asyncio runners can miss the callback that
    ``asyncio.wrap_future`` installs when the same executor is reused for
    sequential operations. Bounded polling keeps the loop responsive and
    lets the caller retain normal cancellation and exception propagation.
    """
    wrapped = asyncio.wrap_future(future)
    try:
        while not future.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(wrapped),
                    timeout=0.01,
                )
            except TimeoutError:
                continue
        return _completed_thread_future_result(future, wrapped)
    except asyncio.CancelledError:
        _observe_cancelled_thread_wait(future, wrapped)
        raise
