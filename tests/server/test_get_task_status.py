# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the non-blocking GetTaskStatus handler.

Pin the Phase 9.3 contract: an unrecorded id reads back as
``"unknown"``; a recorded task merges exactly one live platform
``task_status`` result; and a failed live check degrades to the
locally recorded status rather than raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.mcp.handlers import handle_get_task_status
from mcp_server_phytomni.mcp.schemas import GetTaskStatus
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


def _point_at_tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Redirect the handler's tasks DB to an isolated temp file.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        The temp database path the handler will resolve.
    """
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.handlers.resolve_tasks_db_path",
        lambda: db_path,
    )
    return db_path


async def test_unknown_task_returns_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify an unrecorded task id reads back as ``unknown``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the unknown-status assertions pass.
    """
    _point_at_tmp_db(monkeypatch, tmp_path)

    result = await handle_get_task_status(GetTaskStatus(task_id="nope"))

    assert result["status"] == "unknown"
    assert result["live_status"] is None


async def test_recorded_task_merges_live_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the recorded row is merged with one live status check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the merged-status assertions pass.
    """
    db_path = _point_at_tmp_db(monkeypatch, tmp_path)
    TaskManager(db_path).record_submission("T-9", "submitted", "/obs/o")

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
        "mcp_server_phytomni.mcp.handlers.task_status", fake_status
    )

    result = await handle_get_task_status(GetTaskStatus(task_id="T-9"))

    assert result["status"] == "SUCCEEDED"
    assert result["output_dir"] == "/obs/o"
    assert result["live_status"] == {"status": "SUCCEEDED"}


async def test_live_failure_degrades_to_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify a failed live check falls back to the recorded status.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the degraded-status assertions pass.
    """
    db_path = _point_at_tmp_db(monkeypatch, tmp_path)
    TaskManager(db_path).record_submission("T-7", "submitted", "/obs/x")

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

    monkeypatch.setattr("mcp_server_phytomni.mcp.handlers.task_status", boom)

    result = await handle_get_task_status(GetTaskStatus(task_id="T-7"))

    assert result["status"] == "submitted"
    assert result["output_dir"] == "/obs/x"
    assert result["live_status"] is None
