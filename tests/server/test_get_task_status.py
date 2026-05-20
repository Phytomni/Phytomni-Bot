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

from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.mcp.handlers import handle_get_task_status
from mcp_server_phytomni.mcp.schemas import GetTaskStatus
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


async def test_unknown_task_returns_unknown(tasks_db_path: str) -> None:
    """Verify an unrecorded task id reads back as ``unknown``.

    Args:
        tasks_db_path: Temp registry DB fixture (applies the patch).

    Returns:
        None after the unknown-status assertions pass.
    """
    _ = tasks_db_path

    result = await handle_get_task_status(GetTaskStatus(task_id="nope"))

    assert result["status"] == "unknown"
    assert result["live_status"] is None


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

    assert result["status"] == "submitted"
    assert result["output_dir"] == "/obs/x"
    assert result["live_status"] is None
