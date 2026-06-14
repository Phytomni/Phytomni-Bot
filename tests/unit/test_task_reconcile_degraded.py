# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""``reconcile_task`` surfaces the degraded reason from the row."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.runtime import task_reconcile
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.unit


async def _benign_status(*_args: Any, **_kwargs: Any) -> dict[str, str]:
    """Return a benign live status so reconcile takes the success path."""
    return {"status": "succeeded"}


async def test_reconcile_unknown_task_is_not_degraded(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown id reconciles to a non-degraded row.

    A real (empty) registry is enough: ``reconcile_task`` finds no row
    and returns early, before any live-status probe, so no stub is
    needed.
    """
    db = str(tmp_path / "tasks.db")
    monkeypatch.setattr(task_reconcile, "resolve_tasks_db_path", lambda: db)
    TaskManager(db)  # create the schema; no row for the queried id

    result = await task_reconcile.reconcile_task("nope")

    assert result["degraded"] is False
    assert result["degraded_reason"] is None


async def test_reconcile_surfaces_degraded_reason(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded row reconciles to degraded True + the reason.

    Drives the real persist→reconcile round trip: ``set_task_degraded``
    writes the reason and ``reconcile_task`` reads it back. The live
    status probe is stubbed so the success path runs offline.
    """
    db = str(tmp_path / "tasks.db")
    monkeypatch.setattr(task_reconcile, "resolve_tasks_db_path", lambda: db)
    monkeypatch.setattr(task_reconcile, "task_status", _benign_status)
    mgr = TaskManager(db)
    mgr.record_submission("dg-1", "succeeded", "/obs/x")
    mgr.set_task_degraded("dg-1", "gene overview unavailable")

    result = await task_reconcile.reconcile_task("dg-1")

    assert result["degraded"] is True
    assert result["degraded_reason"] == "gene overview unavailable"
