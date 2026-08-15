# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the non-blocking GetTaskStatus handler.

Pin the GetTaskStatus contract: an unrecorded id reads back as
``"unknown"``; a recorded task merges exactly one live platform
``task_status`` result; and a failed live check degrades to the
locally recorded status rather than raising.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
)
from mcp_server_phytomni.mcp.handlers import handle_get_task_status
from mcp_server_phytomni.mcp.schemas import GetTaskStatus
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


def _seed_partial_deep_genome_snapshot(db_path: str) -> None:
    """Create one live-looking owner with one usable and one failed item."""
    store = DeepGenomeStore(db_path)
    reservation = store.reserve_run(
        run_id="run-public",
        umbrella_task_id="task-public",
        owner="alice",
        output_dir="/obs/public",
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="# BriefGene profile",
    )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
    )
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="running",
    )
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="# SMEP summary",
    )
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smoc_analysis",
        status="failed",
    )


async def test_unknown_task_returns_unknown(tasks_db_path: str) -> None:
    """Verify an unrecorded task id reads back as ``unknown``.

    Args:
        tasks_db_path: Temp registry DB fixture (applies the patch).

    Returns:
        None after the unknown-status assertions pass.
    """
    _ = tasks_db_path

    result = await handle_get_task_status(GetTaskStatus(task_id="nope"))

    assert result is not None
    assert result["status"] == "unknown"
    assert result["live_status"] is None


async def test_deep_genome_snapshot_projects_public_shape(
    tasks_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GetTaskStatus exposes the additive report contract for an umbrella."""
    _seed_partial_deep_genome_snapshot(tasks_db_path)
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.is_live_running",
        lambda _task_id: True,
    )

    result = await handle_get_task_status(GetTaskStatus(task_id="task-public"))

    assert result["status"] == "running"
    assert result["intermediate_report"].startswith("#")
    assert result["final_report"] is None
    assert result["report_stage"] == "intermediate"
    assert result["report_completeness"] == "partial"
    assert result["report_revision"] == 4
    assert result["progress"]["total"] == 12
    assert result["degraded"] is True
    assert result["degraded_reason"] == (
        "1 of 12 optional analyses unavailable"
    )
    assert result["failures"] == [
        {
            "work_item_key": "smoc_analysis",
            "status": "failed",
            "message": "analysis task failed",
        }
    ]


async def test_recorded_task_merges_live_status(
    tasks_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the recorded row is merged with one live status check.

    Args:
        tasks_db_path: Temp registry DB fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the merged-status assertions pass.
    """
    TaskManager(tasks_db_path).record_submission("T-9", "submitted", "/obs/o")

    async def fake_status(*args: Any, **kwargs: Any) -> Any:
        """Return a canned live platform status.

        Args:
            *args: Ignored positional args.
            **kwargs: Ignored keyword args.

        Returns:
            A succeeded platform status payload.
        """
        _ = (args, kwargs)
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status", fake_status
    )

    result = await handle_get_task_status(GetTaskStatus(task_id="T-9"))

    assert result is not None
    assert result["status"] == "SUCCEEDED"
    assert result["output_dir"] == "/obs/o"
    assert result["live_status"] == {"status": "SUCCEEDED"}


async def test_live_failure_degrades_to_recorded(
    tasks_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a failed live check falls back to the recorded status.

    Args:
        tasks_db_path: Temp registry DB fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the degraded-status assertions pass.
    """
    TaskManager(tasks_db_path).record_submission("T-7", "submitted", "/obs/x")

    async def boom(*args: Any, **kwargs: Any) -> Any:
        """Raise as the live platform check would on failure.

        Args:
            *args: Ignored positional args.
            **kwargs: Ignored keyword args.

        Raises:
            McpError: Always, simulating a failed live check.
        """
        _ = (args, kwargs)
        raise McpError(ErrorData(code=INTERNAL_ERROR, message="platform down"))

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status", boom
    )

    result = await handle_get_task_status(GetTaskStatus(task_id="T-7"))

    assert result is not None
    assert result["status"] == "submitted"
    assert result["output_dir"] == "/obs/x"
    assert result["live_status"] is None


async def test_persisted_degraded_reason_surfaces_on_poll(
    tasks_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted degraded reason surfaces on the task poll response.

    GetTaskStatus must read the stored reason through
    ``get_task_degraded`` and expose ``degraded: True`` and
    ``degraded_reason`` while the run itself stays successful because
    degradation is status-independent.

    Args:
        tasks_db_path: Temp registry DB fixture.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the poll-surface degraded assertions pass.
    """
    manager = TaskManager(tasks_db_path)
    manager.record_submission("T-deg", "submitted", "/obs/deg")
    manager.set_task_degraded(
        "T-deg", "literature retrieval degraded for 2 symbols"
    )

    async def fake_status(*args: Any, **kwargs: Any) -> Any:
        """Return a succeeded live status (degraded != failed)."""
        _ = (args, kwargs)
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.task_reconcile.task_status", fake_status
    )

    result = await handle_get_task_status(GetTaskStatus(task_id="T-deg"))

    assert result is not None
    assert result["status"] == "SUCCEEDED"
    assert result["degraded"] is True
    assert (
        result["degraded_reason"]
        == "literature retrieval degraded for 2 symbols"
    )
