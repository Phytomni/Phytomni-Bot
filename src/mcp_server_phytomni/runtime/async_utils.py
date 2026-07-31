# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small asyncio coordination helpers for runtime worker threads."""

from __future__ import annotations

import asyncio


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
