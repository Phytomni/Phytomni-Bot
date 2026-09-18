# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Retain validated scientific output through unsuccessful reassembly."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.mcp.formatting.models import (
    ResultArchiveDescriptor,
    ResultDelivery,
)
from mcp_server_phytomni.runtime.artifact_roles import ClassifiedArtifact
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    ReportArtifactSources,
    ReportSettlementRequest,
    settle_report_terminal,
)
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportAssembly,
    TerminalReportContext,
    assemble_terminal_report,
)
from mcp_server_phytomni.storage.artifact_listing import ListedArtifactObject

pytestmark = pytest.mark.unit

SCIENCE = (
    "# Results\n\nThe failed experiments support a context-dependent "
    "effect <sup>2</sup>."
)
ROOT = "/obs/synthetic-bucket/alice/run-retained"
NEW_SCIENCE = (
    "# Results\n\nThe independent new measurements confirmed the effect."
)
REFERENCES = [
    {"file_id": "unused"},
    {"file_id": "evidence", "title": "Synthetic evidence"},
]
ARTIFACT = {
    "name": "evidence.tsv",
    "role": "scientific_table",
    "media_type": "text/tab-separated-values",
    "size_bytes": 32,
    "downloadable": True,
    "report_context_eligible": True,
    "download_ref": ROOT + "/evidence.tsv",
}


def _initial_result(answer: str, ready: bool) -> dict[str, Any]:
    digest = "sha256:" + "a" * 64
    archive = ResultArchiveDescriptor(
        "result_archive",
        "analyst-results.zip",
        "application/zip",
        32,
        True,
        False,
        "result-archive:" + digest,
    )
    return {
        "formatted": {"answer": answer, "references": REFERENCES},
        "execution": {
            "report": {
                "state": "final",
                "degraded": False,
                "source_artifact_count": 1,
            },
            "artifacts": [ARTIFACT],
            "output_dirs": [ROOT],
            "delivery": (
                asdict(
                    ResultDelivery(
                        1, True, "ready", 1, digest, archive, None, False
                    )
                )
                if ready
                else None
            ),
        },
    }


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize(
    "later", ["no_text", "synthesis_failure", "new_science"]
)
async def test_settlement_retains_existing_science_and_independent_files(
    tmp_path: Path, ready: bool, later: str
) -> None:
    """An empty later assembly cannot erase admitted science or delivery."""
    initial = _initial_result(SCIENCE, ready)
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-retained", "alice", "analyst", "remote"),
        outcome=RunOutcome("running", initial),
    )
    manager = TaskManager(registry.db_path)
    manager.record(Submission("child-retained", "succeeded", ROOT))
    manager.set_task_final_report("child-retained", SCIENCE)
    live = [
        {
            "task_id": "child-retained",
            "status": "succeeded",
            "output_dir": ROOT,
            "final_report": SCIENCE,
        }
    ]

    async def lister(_output_dir: str) -> list[ListedArtifactObject]:
        running = registry.get_run("run-retained", owner="alice")
        assert running is not None and running.result is not None
        assert running.result["formatted"]["answer"] == SCIENCE
        assert running.result["formatted"]["references"] == REFERENCES
        assert manager.get_task_final_report("child-retained") == SCIENCE
        return (
            []
            if later == "no_text"
            else [
                ListedArtifactObject(
                    "new.md", ROOT + "/new.md", 32, ROOT + "/new.md"
                )
            ]
        )

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {
            "version": "1.0",
            "artifacts": (
                []
                if later == "no_text"
                else [
                    {
                        "path": "new.md",
                        "role": "scientific_report",
                        "media_type": "text/markdown",
                    }
                ]
            ),
        }

    async def reader(_reference: str) -> str:
        return "Independent scientific measurements."

    async def summarizer(_prompt: str) -> str:
        if later == "synthesis_failure":
            raise TimeoutError("synthetic model timeout")
        assert later == "new_science"
        return NEW_SCIENCE

    async def assemble(
        *,
        context: TerminalReportContext,
        artifacts: tuple[ClassifiedArtifact, ...],
    ) -> TerminalReportAssembly:
        return await assemble_terminal_report(
            context=context,
            artifacts=artifacts,
            reader=reader,
            summarizer=summarizer,
        )

    settled = await settle_report_terminal(
        ReportSettlementRequest(
            registry,
            registry.get_run("run-retained", owner="alice"),
            "succeeded",
            live,
            ReportArtifactSources(
                object_lister=lister, manifest_loader=manifest_loader
            ),
            assembler=assemble,
        )
    )
    assert settled is not None and settled.result is not None
    assert settled.status == "succeeded"
    expected = NEW_SCIENCE if later == "new_science" else SCIENCE
    assert settled.result["formatted"]["answer"] == expected
    assert manager.get_task_final_report("child-retained") == expected
    assert live[0]["final_report"] == expected
    assert settled.result["formatted"]["references"] == (
        [] if later == "new_science" else REFERENCES
    )
    execution = settled.result["execution"]
    assert ARTIFACT in execution["artifacts"]
    assert ROOT in execution["output_dirs"]
    assert execution["delivery"] == initial["execution"]["delivery"]
    assert execution["report"]["source_artifact_count"] == 1
    assert execution["report"]["degraded"] is (later != "new_science")
    assert execution["report"]["state"] == "final"


@pytest.mark.parametrize(
    "answer",
    [
        "Analysis complete: 1/1 tasks succeeded.",
        "LLM summary failed: TimeoutError",
        "No readable text artifacts were available for LLM summary",
        "Execution log: task failed.",
        "Provider: synthetic-service",
        "Result unavailable at /tmp/private-result",
        "The analysis reached a terminal outcome, but no validated "
        "scientific text artifact was available for synthesis.",
        "The analysis reached a terminal outcome, but scientific report "
        "synthesis was unavailable.",
        "\u5206\u6790\u5df2\u5230\u8fbe\u7ec8\u6001\uff0c\u4f46"
        "\u6ca1\u6709\u53ef\u7528\u4e8e\u7efc\u5408\u7684\u5df2"
        "\u9a8c\u8bc1\u79d1\u5b66\u6587\u672c\u4ea7\u7269\u3002",
        "\u5206\u6790\u5df2\u5230\u8fbe\u7ec8\u6001\uff0c\u4f46"
        "\u79d1\u5b66\u62a5\u544a\u7efc\u5408\u4e0d\u53ef\u7528\u3002",
    ],
)
async def test_empty_assembly_does_not_revive_operational_report(
    tmp_path: Path,
    answer: str,
) -> None:
    """Retained archive delivery does not make an old failure body science."""
    initial = _initial_result(answer, True)
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-retained", "alice", "analyst", "remote"),
        outcome=RunOutcome("running", initial),
    )
    current = registry.get_run("run-retained", owner="alice")
    settled = await settle_report_terminal(
        ReportSettlementRequest(
            registry,
            current,
            "failed",
            [],
            ReportArtifactSources(),
        )
    )
    assert settled.status == "failed"
    assert settled.result["formatted"]["answer"] == ""
    assert settled.result["formatted"]["references"] == []
    assert (
        settled.result["execution"]["delivery"]
        == initial["execution"]["delivery"]
    )
    assert ARTIFACT in settled.result["execution"]["artifacts"]


@pytest.mark.parametrize(
    "report", [None, {"state": "invalid", "degraded": True}, {"state": {}}]
)
async def test_retention_requires_canonical_report_state(
    tmp_path: Path,
    report: Any,
) -> None:
    """A body without a recognized report state is not admitted science."""
    initial = _initial_result(SCIENCE, False)
    initial["execution"]["report"] = report
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-retained", "alice", "analyst", "remote"),
        outcome=RunOutcome("running", initial),
    )
    current = registry.get_run("run-retained", owner="alice")
    settled = await settle_report_terminal(
        ReportSettlementRequest(
            registry,
            current,
            "succeeded",
            [],
            ReportArtifactSources(),
        )
    )
    assert settled.result["formatted"]["answer"] == ""
    assert settled.result["formatted"]["references"] == []


async def test_failed_run_retains_science_without_reading_failed_sources(
    tmp_path: Path,
) -> None:
    """Real prior science survives failure without admitting failed outputs."""
    initial = _initial_result(SCIENCE, True)
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-retained", "alice", "analyst", "remote"),
        outcome=RunOutcome("running", initial),
    )
    current = registry.get_run("run-retained", owner="alice")

    async def lister(_output_dir: str) -> list[ListedArtifactObject]:
        pytest.fail("Failed child outputs must not be listed")

    settled = await settle_report_terminal(
        ReportSettlementRequest(
            registry,
            current,
            "failed",
            [
                {
                    "task_id": "failed-child",
                    "status": "failed",
                    "output_dir": ROOT,
                }
            ],
            ReportArtifactSources(object_lister=lister),
        )
    )
    assert settled.status == "failed"
    assert settled.result["formatted"]["answer"] == SCIENCE
    assert settled.result["formatted"]["references"] == REFERENCES
    assert settled.result["execution"]["report"]["degraded"] is True
    assert (
        settled.result["execution"]["delivery"]
        == initial["execution"]["delivery"]
    )


async def test_stale_retention_cannot_overwrite_terminal_winner(
    tmp_path: Path,
) -> None:
    """Settlement losing the terminal CAS never updates child report text."""
    initial = _initial_result(SCIENCE, True)
    registry = RunRegistry(str(tmp_path / "runs.sqlite"))
    registry.create_run(
        RunSpec("run-retained", "alice", "analyst", "remote"),
        outcome=RunOutcome("running", initial),
    )
    current = registry.get_run("run-retained", owner="alice")
    assert current is not None
    manager = TaskManager(registry.db_path)
    manager.record(Submission("child-retained", "succeeded", ROOT))
    manager.set_task_final_report("child-retained", SCIENCE)
    winner = {"formatted": {"answer": "Separate terminal winner."}}

    async def lister(_output_dir: str) -> list[ListedArtifactObject]:
        assert registry.settle_run(
            "run-retained",
            owner="alice",
            status="failed",
            result=winner,
            expected_revision=current.revision,
        )
        return []

    async def manifest_loader(_output_dir: str) -> dict[str, Any]:
        return {"version": "1.0", "artifacts": []}

    settled = await settle_report_terminal(
        ReportSettlementRequest(
            registry,
            current,
            "succeeded",
            [
                {
                    "task_id": "child-retained",
                    "status": "succeeded",
                    "output_dir": ROOT,
                }
            ],
            ReportArtifactSources(
                object_lister=lister, manifest_loader=manifest_loader
            ),
        )
    )
    assert settled.status == "failed"
    assert settled.result == winner
    assert manager.get_task_final_report("child-retained") == SCIENCE
