# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Partial and cancelled child aggregation pins for run reconcile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_run_registry import _make_registry, _seed_async_run

from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.run_registry import RunSpec
from mcp_server_phytomni.runtime.run_registry_models import (
    _PARTIAL_CHILDREN_FAILED,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    attach_partial_child_degraded,
)

pytestmark = pytest.mark.unit


async def _noop_lister(_output_dir: str) -> list[Any]:
    """Avoid OBS I/O while reconciling canned child statuses."""
    return []


async def _reconcile_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec: RunSpec,
    live: dict[str, dict[str, Any]],
) -> Any:
    """Seed one run and reconcile it against canned child statuses."""
    packed = _make_registry(tmp_path)
    _seed_async_run(packed[0], packed[1], spec, tuple(live))

    async def _status(task_id: str) -> dict[str, Any]:
        return dict(live[task_id])

    monkeypatch.setattr(run_registry, "reconcile_task", _status)
    return await packed[0].reconcile(
        spec.run_id, owner=spec.user_id, lister=_noop_lister
    )


def test_attach_partial_child_degraded_is_idempotent() -> None:
    """A second mark must not duplicate the bounded warning."""
    first = attach_partial_child_degraded(None)
    second = attach_partial_child_degraded(first)
    warnings = second["execution"]["warnings"]
    assert second["execution"]["tracking"]["degraded"] is True
    assert [item["code"] for item in warnings] == [_PARTIAL_CHILDREN_FAILED]


@pytest.mark.asyncio
async def test_reconcile_partial_success_stays_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful child keeps the run succeeded when a sibling failed."""
    record = await _reconcile_live(
        tmp_path,
        monkeypatch,
        RunSpec("run-partial", "alice", "design", "remote"),
        {
            "t-ok": {
                "task_id": "t-ok",
                "status": "succeeded",
                "output_dir": "/obs/a",
            },
            "t-bad": {
                "task_id": "t-bad",
                "status": "failed",
                "output_dir": "",
            },
        },
    )
    assert record is not None
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.result["execution"]["tracking"]["degraded"] is True
    statuses = {
        row["id"]: row["status"] for row in record.result["execution"]["tasks"]
    }
    assert statuses == {"t-ok": "succeeded", "t-bad": "failed"}


@pytest.mark.asyncio
async def test_reconcile_all_cancelled_children_is_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every cancelled child settles the umbrella as cancelled."""
    record = await _reconcile_live(
        tmp_path,
        monkeypatch,
        RunSpec("run-can", "alice", "analyst", "remote"),
        {"t-1": {"task_id": "t-1", "status": "cancelled", "output_dir": ""}},
    )
    assert record is not None
    assert record.status == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_mixed_cancelled_and_running_stays_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-running sibling keeps the umbrella running."""
    record = await _reconcile_live(
        tmp_path,
        monkeypatch,
        RunSpec("run-mix", "alice", "research", "remote"),
        {
            "t-1": {"task_id": "t-1", "status": "cancelled", "output_dir": ""},
            "t-2": {"task_id": "t-2", "status": "submitted", "output_dir": ""},
        },
    )
    assert record is not None
    assert record.status == "running"
