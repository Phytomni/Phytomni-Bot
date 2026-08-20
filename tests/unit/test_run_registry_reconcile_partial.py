# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Partial and cancelled child aggregation pins for run reconcile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support.execution_tasks import execution_child
from tests.unit.test_run_registry import _make_registry, _seed_async_run

from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.run_registry import RunSpec
from mcp_server_phytomni.runtime.run_registry_models import (
    _PARTIAL_CHILDREN_FAILED,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    attach_partial_child_degraded,
    overlay_live_status_on_stored_tasks,
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
    result: dict[str, Any] | None = None,
) -> Any:
    """Seed one run and reconcile it against canned child statuses."""
    packed = _make_registry(tmp_path)
    _seed_async_run(packed[0], packed[1], spec, tuple(live))
    if result is not None:
        assert packed[0].update_running_result(
            spec.run_id, owner=spec.user_id, result=result
        )

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


@pytest.mark.asyncio
async def test_reconcile_cancelled_plus_doomed_failed_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted cancelled plus stored doomed failed settles failed."""
    record = await _reconcile_live(
        tmp_path,
        monkeypatch,
        RunSpec("run-doomed-fail", "alice", "design", "remote"),
        {
            "t-cancel": {
                "task_id": "t-cancel",
                "status": "cancelled",
                "output_dir": "",
            }
        },
        result={
            "execution": {
                "tasks": [
                    execution_child(
                        "t-cancel",
                        kind="protein_design_analysis",
                    ),
                    execution_child(
                        "rejected-t-fail",
                        accepted=False,
                        status="failed",
                        kind="promoter_design_analysis",
                        error_code="input_rejected",
                    ),
                ]
            }
        },
    )
    assert record is not None
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_running_umbrella_overlays_live_failed_child_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live failed sibling updates stored execution.tasks while running."""
    record = await _reconcile_live(
        tmp_path,
        monkeypatch,
        RunSpec("run-live-fail", "alice", "design", "remote"),
        {
            "t-ok": {
                "task_id": "t-ok",
                "status": "submitted",
                "output_dir": "",
            },
            "t-bad": {
                "task_id": "t-bad",
                "status": "failed",
                "output_dir": "",
            },
        },
        result={
            "execution": {
                "tasks": [
                    execution_child(
                        "t-ok",
                        kind="protein_design_analysis",
                    ),
                    execution_child(
                        "t-bad",
                        kind="promoter_design_analysis",
                    ),
                ]
            }
        },
    )
    assert record is not None
    assert record.status == "running"
    assert record.result is not None
    tasks = {row["id"]: row for row in record.result["execution"]["tasks"]}
    assert tasks["t-ok"]["status"] == "submitted"
    assert tasks["t-ok"]["kind"] == "protein_design_analysis"
    assert tasks["t-ok"]["accepted"] is True
    assert tasks["t-ok"]["error_code"] is None
    assert tasks["t-bad"]["status"] == "failed"
    assert tasks["t-bad"]["kind"] == "promoter_design_analysis"
    assert tasks["t-bad"]["accepted"] is True
    assert tasks["t-bad"]["error_code"] is None


def test_overlay_live_status_skips_unusable_inputs() -> None:
    """Missing live rows or stored tasks leave the projection untouched."""
    assert overlay_live_status_on_stored_tasks({"execution": {}}, None) is None
    assert overlay_live_status_on_stored_tasks(None, []) is None
    assert overlay_live_status_on_stored_tasks({"execution": {}}, []) is None
    assert (
        overlay_live_status_on_stored_tasks(
            {"execution": {"tasks": [execution_child("t-ok", kind="kind")]}},
            [{"task_id": "", "status": "failed"}],
        )
        is None
    )


def test_overlay_live_status_copies_status_onto_stored_rows() -> None:
    """Live status overwrites the matching stored execution.tasks row."""
    stored = {
        "execution": {
            "tasks": [execution_child("t-ok", kind="protein_design_analysis")]
        }
    }
    overlay = overlay_live_status_on_stored_tasks(
        stored,
        [{"task_id": "t-ok", "status": "failed"}],
    )
    assert overlay is not None
    assert overlay["execution"]["tasks"][0]["status"] == "failed"
    assert overlay["execution"]["tasks"][0]["kind"] == (
        "protein_design_analysis"
    )
