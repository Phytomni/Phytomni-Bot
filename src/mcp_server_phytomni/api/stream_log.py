# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Process-local AG-UI SSE buffer for detached chat-completion runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

__all__ = [
    "RunStreamLog",
    "bind_run_stream_log",
    "drop_run_stream_log",
    "iter_run_stream",
    "schedule_run_stream_log_drop",
]

RUN_STREAM_REPLAY_RETENTION_SECONDS = 15 * 60


class RunStreamLog:
    """Buffered AG-UI SSE frames for one producer and its subscribers."""

    def __init__(self) -> None:
        self._frames: list[str] = []
        self._done = False
        self._waiters: list[asyncio.Event] = []

    def append(self, frame: str) -> None:
        """Record one SSE frame and wake live followers."""
        self._frames.append(frame)
        self._wake()

    def close(self) -> None:
        """Mark the producer finished and wake live followers."""
        self._done = True
        self._wake()

    def _wake(self) -> None:
        waiters = self._waiters
        self._waiters = []
        for waiter in waiters:
            waiter.set()

    async def follow(self, after: int) -> AsyncIterator[str]:
        """Replay frames with seq > ``after``, then tail until close."""
        index = after if after > 0 else 0
        while True:
            if index < len(self._frames):
                frame = self._frames[index]
                index += 1
                yield frame
                continue
            if self._done:
                return
            waiter = asyncio.Event()
            self._waiters.append(waiter)
            try:
                await waiter.wait()
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)


_STREAM_LOGS: dict[str, RunStreamLog] = {}
_DROP_HANDLES: dict[str, asyncio.TimerHandle] = {}


def bind_run_stream_log(run_id: str, log: RunStreamLog) -> None:
    """Publish one live stream log under ``run_id``."""
    handle = _DROP_HANDLES.pop(run_id, None)
    if handle is not None:
        handle.cancel()
    _STREAM_LOGS[run_id] = log


def drop_run_stream_log(run_id: str) -> None:
    """Drop the live stream log for ``run_id`` if present."""
    handle = _DROP_HANDLES.pop(run_id, None)
    if handle is not None:
        handle.cancel()
    _STREAM_LOGS.pop(run_id, None)


def schedule_run_stream_log_drop(
    run_id: str,
    *,
    delay: float = RUN_STREAM_REPLAY_RETENTION_SECONDS,
) -> None:
    """Retain a completed replay log until its bounded expiry."""
    log = _STREAM_LOGS.get(run_id)
    if log is None:
        return
    previous = _DROP_HANDLES.pop(run_id, None)
    if previous is not None:
        previous.cancel()

    def _expire() -> None:
        _DROP_HANDLES.pop(run_id, None)
        if _STREAM_LOGS.get(run_id) is log:
            _STREAM_LOGS.pop(run_id, None)

    _DROP_HANDLES[run_id] = asyncio.get_running_loop().call_later(
        max(delay, 0.0),
        _expire,
    )


def iter_run_stream(run_id: str, after: int = 0) -> AsyncIterator[str] | None:
    """Replay then tail the detached SSE log for one run, if present."""
    log = _STREAM_LOGS.get(run_id)
    if log is None:
        return None
    return log.follow(after if after > 0 else 0)
