# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bound request cleanup without replacing the caller's terminal outcome."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Final

import anyio

logger = logging.getLogger(__name__)

CLEANUP_TIMEOUT_SECONDS: Final[float] = 1.0
CLEANUP_SHUTDOWN_TIMEOUT_SECONDS: Final[float] = 1.0
_CLEANUP_FAILURES: tuple[type[Exception], ...] = (Exception,)


class CleanupLifecycleError(RuntimeError):
    """Raised when process-owned cleanup cannot drain before shutdown."""


@dataclass(slots=True)
class _CleanupTaskOwner:
    """Hold overdue cleanup tasks until they complete or serving stops."""

    tasks: set[asyncio.Future[Any]] = field(default_factory=set)

    def adopt(self, task: asyncio.Future[Any]) -> None:
        """Take strong ownership and remove the task after observation."""
        self.tasks.add(task)
        task.add_done_callback(self._observe_and_discard)

    def _observe_and_discard(self, task: asyncio.Future[Any]) -> None:
        """Consume terminal detail without exposing it to the request path."""
        self.tasks.discard(task)
        _observe_task_result(task)


_OWNERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, _CleanupTaskOwner
] = weakref.WeakKeyDictionary()


def _observe_task_result(task: asyncio.Future[Any]) -> None:
    """Retrieve a lifecycle-owned cleanup result without surfacing detail."""
    if task.cancelled():
        return
    with suppress(Exception):
        task.exception()


def _own_overdue_cleanup(task: asyncio.Future[Any]) -> None:
    """Cancel and transfer overdue work to the serving lifecycle owner."""
    loop = task.get_loop()
    owner = _OWNERS.setdefault(loop, _CleanupTaskOwner())
    if task not in owner.tasks:
        owner.adopt(task)
    if not task.done():
        task.cancel()


def pending_cleanup_count() -> int:
    """Return overdue cleanup tasks owned by the current event loop."""
    owner = _OWNERS.get(asyncio.get_running_loop())
    return 0 if owner is None else len(owner.tasks)


async def aclose_cleanup_runtime() -> None:
    """Drain process-owned overdue cleanup before serving shutdown completes.

    A task that suppresses cancellation cannot be killed safely by Python. It
    therefore remains explicitly owned by this serving lifecycle instead of
    being detached. A bounded shutdown that cannot drain it fails closed so
    the process supervisor can terminate the unhealthy process.
    """
    owner = _OWNERS.get(asyncio.get_running_loop())
    if owner is None or not owner.tasks:
        return
    tasks = tuple(owner.tasks)
    for task in tasks:
        task.cancel()
    _done, pending = await asyncio.wait(
        tasks,
        timeout=CLEANUP_SHUTDOWN_TIMEOUT_SECONDS,
    )
    if pending:
        logger.error(
            "stream cleanup shutdown incomplete pending_count=%d",
            len(pending),
        )
        raise CleanupLifecycleError("stream cleanup did not drain")


async def run_bounded_cleanup(
    awaitable: Awaitable[Any],
    *,
    operation: str,
    cancelled_is_success: bool = False,
) -> bool:
    """Await cleanup under one shielded deadline and log only safe metadata.

    Returns ``True`` when cleanup completed, including an expected cancellation
    of an in-flight ``anext`` task. Cleanup exceptions and timeouts are logged
    by operation and exception class only, then suppressed so they cannot
    replace the request's original cancellation or body failure.
    """
    task = asyncio.ensure_future(awaitable)
    scope: anyio.CancelScope | None = None
    try:
        with anyio.move_on_after(
            CLEANUP_TIMEOUT_SECONDS,
            shield=True,
        ) as scope:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done() and task.cancelled():
                    if cancelled_is_success:
                        return True
                    logger.warning(
                        "stream cleanup cancelled operation=%s",
                        operation,
                    )
                    return False
                _own_overdue_cleanup(task)
                raise
            except _CLEANUP_FAILURES as exc:
                logger.warning(
                    "stream cleanup failed operation=%s exception=%s",
                    operation,
                    type(exc).__name__,
                )
                return False
    except asyncio.CancelledError:
        _own_overdue_cleanup(task)
        raise

    if scope is not None and scope.cancel_called:
        _own_overdue_cleanup(task)
        logger.warning(
            "stream cleanup timed out operation=%s timeout_seconds=%.1f",
            operation,
            CLEANUP_TIMEOUT_SECONDS,
        )
        return False
    return True


__all__ = [
    "CLEANUP_SHUTDOWN_TIMEOUT_SECONDS",
    "CLEANUP_TIMEOUT_SECONDS",
    "CleanupLifecycleError",
    "aclose_cleanup_runtime",
    "pending_cleanup_count",
    "run_bounded_cleanup",
]
