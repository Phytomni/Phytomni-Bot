# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Extra reconcile edges kept out of the main module line cap."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_run_registry import _make_registry, _seed_async_run
from tests.unit.test_run_registry_reconcile import (
    _empty_lister,
    _FakeReportResult,
)

from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.run_registry import RunSpec

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_reconcile_non_target_agent_skips_terminal_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-analyst-class agents never invoke terminal report synthesis."""
    registry, manager, _unused = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-chat", "alice", "chat", "remote"),
        ("task-1",),
    )
    called = {"n": 0}

    async def fake_reconcile_task(task_id: str) -> dict[str, Any]:
        """Return a succeeded task row."""
        return {
            "task_id": task_id,
            "status": "succeeded",
            "output_dir": "/obs/bucket/out",
        }

    async def tracking_synthesize(_context: Any) -> Any:
        """Track that synthesis was invoked (should not happen here)."""
        called["n"] += 1
        return _FakeReportResult()

    monkeypatch.setattr(run_registry, "reconcile_task", fake_reconcile_task)
    monkeypatch.setattr(
        run_registry,
        "assemble_terminal_report",
        tracking_synthesize,
    )

    record = await registry.reconcile(
        "run-chat", owner="alice", lister=_empty_lister
    )

    assert record is not None
    assert called["n"] == 0
    assert record.result is not None
    assert record.result["final_report"] is None
