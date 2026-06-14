# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""``reconcile_task`` surfaces the degraded reason from the row."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.runtime import task_reconcile

pytestmark = pytest.mark.unit


class _UnknownManager:
    """Stand-in TaskManager whose row lookup always misses."""

    def __init__(self, _db: str) -> None:
        pass

    def get_task(self, _task_id: str) -> None:
        """Always miss the row lookup."""
        return None


class _DegradedManager:
    """Stand-in TaskManager returning a degraded recorded row."""

    def __init__(self, _db: str) -> None:
        pass

    def get_task(self, _task_id: str) -> dict[str, str]:
        """Return a recorded succeeded row."""
        return {
            "task_id": "dg-1",
            "status": "succeeded",
            "analysis_id": "a",
            "output_dir": "/obs/x",
        }

    def get_task_final_report(self, _task_id: str) -> str:
        """Return a canned report markdown."""
        return "# report"

    def get_task_degraded(self, _task_id: str) -> str:
        """Return a degraded reason."""
        return "gene overview unavailable"


async def _benign_status(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    """Return a benign live status so reconcile takes the success path."""
    return {"status": "succeeded"}


async def test_reconcile_unknown_task_is_not_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown id reconciles to a non-degraded row."""
    monkeypatch.setattr(task_reconcile, "TaskManager", _UnknownManager)
    monkeypatch.setattr(
        task_reconcile, "resolve_tasks_db_path", lambda: ":memory:"
    )

    result = await task_reconcile.reconcile_task("nope")

    assert result["degraded"] is False
    assert result["degraded_reason"] is None


async def test_reconcile_surfaces_degraded_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded row reconciles to degraded True + the reason."""
    monkeypatch.setattr(task_reconcile, "TaskManager", _DegradedManager)
    monkeypatch.setattr(
        task_reconcile, "resolve_tasks_db_path", lambda: ":memory:"
    )
    monkeypatch.setattr(task_reconcile, "task_status", _benign_status)

    result = await task_reconcile.reconcile_task("dg-1")

    assert result["degraded"] is True
    assert result["degraded_reason"] == "gene overview unavailable"
