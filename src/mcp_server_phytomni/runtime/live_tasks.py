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
    "clear_cancel_requested",
    "deregister_live_task",
    "is_cancel_requested",
    "is_live_running",
    "register_live_task",
    "request_cancel",
]

_LIVE: dict[str, asyncio.Task[object]] = {}
_CANCEL_REQUESTED: set[str] = set()


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


def request_cancel(task_id: str) -> bool:
    """Mark ``task_id`` cancelled and cancel its live worker if present."""
    if task_id:
        _CANCEL_REQUESTED.add(task_id)
    return cancel_live_task(task_id)


def is_cancel_requested(task_id: str) -> bool:
    """Return True when an owner asked to cancel ``task_id``."""
    return bool(task_id) and task_id in _CANCEL_REQUESTED


def clear_cancel_requested(task_id: str) -> None:
    """Drop a cancel flag after the worker has observed it."""
    _CANCEL_REQUESTED.discard(task_id)
