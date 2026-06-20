# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the in-flight umbrella registry (runtime/live_tasks)."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_phytomni.runtime.live_tasks import (
    deregister_live_task,
    is_live_running,
    register_live_task,
)

pytestmark = pytest.mark.unit


async def test_registered_unfinished_task_is_live() -> None:
    """A registered, still-running task reads as live."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(_hang())
    register_live_task("u1", task)
    try:
        assert is_live_running("u1") is True
    finally:
        task.cancel()
        deregister_live_task("u1")


def test_absent_id_is_not_live() -> None:
    """An id that was never registered is not live."""
    assert is_live_running("never-registered") is False


async def test_done_task_is_not_live() -> None:
    """A registered task that has completed reads as not live."""

    async def _quick() -> int:
        return 1

    task = asyncio.create_task(_quick())
    await task
    register_live_task("u2", task)
    try:
        assert is_live_running("u2") is False
    finally:
        deregister_live_task("u2")


def test_deregister_absent_id_is_noop() -> None:
    """Deregistering an unknown id does not raise."""
    deregister_live_task("never-registered")
