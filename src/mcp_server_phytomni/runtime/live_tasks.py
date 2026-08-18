# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Process-local registry of in-flight local background workflow tasks.

deep_genome runs its umbrella as a background ``asyncio.Task`` whose
terminal-status write is best-effort (WAL lock / full disk), and a
restart kills it; either way the ``tasks`` row can stay non-terminal.
This lets ``runtime.task_reconcile`` tell a live umbrella from a dead one
without a guessed timeout: absent or ``task.done()`` means dead. The
umbrella has no durable resume, so process liveness equals run liveness.
"""

from __future__ import annotations

import asyncio

__all__ = [
    "cancel_live_task",
    "deregister_live_task",
    "is_live_running",
    "register_live_task",
]

_LIVE: dict[str, asyncio.Task[object]] = {}


def register_live_task(task_id: str, task: asyncio.Task[object]) -> None:
    """Record an in-flight local-workflow task under ``task_id``."""
    _LIVE[task_id] = task


def deregister_live_task(task_id: str) -> None:
    """Drop ``task_id`` from the registry; a missing key is a no-op."""
    _LIVE.pop(task_id, None)


def is_live_running(task_id: str) -> bool:
    """Return True iff ``task_id`` is registered and not yet done."""
    task = _LIVE.get(task_id)
    return task is not None and not task.done()


def cancel_live_task(task_id: str) -> bool:
    """Request cancellation of one registered in-process worker.

    Returns True only when a live task accepted ``Task.cancel()``.
    A missing or already-done id is a no-op.
    """
    task = _LIVE.get(task_id)
    if task is None or task.done():
        return False
    return task.cancel()
