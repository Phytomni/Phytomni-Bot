# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bound request cleanup without replacing the caller's terminal outcome."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any, Final

import anyio

logger = logging.getLogger(__name__)

CLEANUP_TIMEOUT_SECONDS: Final[float] = 1.0
_CLEANUP_FAILURES: tuple[type[Exception], ...] = (Exception,)


def _observe_task_result(task: asyncio.Future[Any]) -> None:
    """Retrieve a detached cleanup result without surfacing its detail."""
    if task.cancelled():
        return
    with suppress(Exception):
        task.exception()


def _detach_cleanup(task: asyncio.Future[Any]) -> None:
    """Cancel and safely observe cleanup that exceeded its ownership window."""
    if not task.done():
        task.cancel()
    task.add_done_callback(_observe_task_result)


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
                _detach_cleanup(task)
                raise
            except _CLEANUP_FAILURES as exc:
                logger.warning(
                    "stream cleanup failed operation=%s exception=%s",
                    operation,
                    type(exc).__name__,
                )
                return False
    except asyncio.CancelledError:
        _detach_cleanup(task)
        raise

    if scope is not None and scope.cancel_called:
        _detach_cleanup(task)
        logger.warning(
            "stream cleanup timed out operation=%s timeout_seconds=%.1f",
            operation,
            CLEANUP_TIMEOUT_SECONDS,
        )
        return False
    return True


__all__ = ["CLEANUP_TIMEOUT_SECONDS", "run_bounded_cleanup"]
