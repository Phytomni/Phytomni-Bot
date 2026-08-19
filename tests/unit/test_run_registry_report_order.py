# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Report settlement order relative to OBS listing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.mcp.formatting.models import ReportExecution
from mcp_server_phytomni.runtime import run_registry, run_registry_reports
from mcp_server_phytomni.runtime.run_registry import RunSpec
from mcp_server_phytomni.runtime.terminal_report import TerminalReportAssembly
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_settle_publishes_report_before_listing_output_dir(
    tmp_path: Path,
) -> None:
    """EI success must surface a report before harvest lists OBS objects.

    Listing a shared dump can hang or pull a large tree. The scientific
    answer has to be persisted first so Web can leave the submit-ack
    wait without a local zip of the output directory.
    """
    registry = run_registry.RunRegistry(str(tmp_path / "runs.db"))
    registry.create_run(
        RunSpec("run-early-report", "alice", "analyst", "remote")
    )
    current = registry.get_run("run-early-report", owner="alice")
    assert current is not None
    order: list[str] = []

    async def lister(_output_dir: str) -> list[ListedArtifactObject]:
        running = registry.get_run("run-early-report", owner="alice")
        assert running is not None
        assert running.result is not None
        assert running.result.get("final_report")
        order.append("list")
        return []

    async def assemble(**_: Any) -> TerminalReportAssembly:
        order.append("assemble")
        return TerminalReportAssembly(
            answer="EI finished",
            report=ReportExecution(state="final"),
        )

    settled = await run_registry_reports.settle_report_terminal(
        run_registry_reports.ReportSettlementRequest(
            registry=registry,
            current=current,
            status="succeeded",
            live=[
                {
                    "task_id": "task-early",
                    "status": "succeeded",
                    "output_dir": "/obs/run/early",
                }
            ],
            sources=run_registry_reports.ReportArtifactSources(
                object_lister=lister
            ),
            assembler=assemble,
        )
    )

    assert order == ["list", "assemble"]
    assert settled is not None
    assert settled.result is not None
    formatted = settled.result.get("formatted")
    assert isinstance(formatted, dict)
    assert formatted.get("answer") == "EI finished"
