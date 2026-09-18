# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Persistence and artifact-reference helpers for terminal reports."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime import terminal_report as report_mod
from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.locale import SupportedLocale
from mcp_server_phytomni.runtime.run_registry_reports import (
    persist_report_compatibility,
)
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportContext,
    TerminalReportResult,
    persist_terminal_report,
    synthesize_terminal_report,
)

pytestmark = pytest.mark.unit


def _synthesis_context(
    agent: str, locale: SupportedLocale, scenario: str
) -> TerminalReportContext:
    """Build admitted synthetic inputs for the persistence matrix."""
    artifact = ClassifiedArtifact(
        source_path="fixture://result.md",
        relative_path="result.md",
        role=ArtifactRole.SCIENTIFIC_REPORT,
        media_type="text/markdown",
        size_bytes=32,
        download_ref="download://result.md",
    )
    return TerminalReportContext(
        agent=agent,
        status="succeeded",
        live=[{"task_id": "task-fixture", "status": "succeeded"}],
        artifacts=() if scenario == "no_text" else (artifact,),
        query="compare synthetic measurements",
        locale=locale,
    )


@pytest.mark.parametrize("agent", ["analyst", "research", "network", "design"])
@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
@pytest.mark.parametrize(
    "scenario",
    ["no_text", "timeout", "error", "empty", "operational", "valid"],
)
@pytest.mark.asyncio
async def test_scientific_answer_survives_real_synthesis_and_persistence(
    tmp_path: Path, agent: str, locale: SupportedLocale, scenario: str
) -> None:
    """Persist science or empty text, never an operational failure body."""
    context = _synthesis_context(agent, locale, scenario)
    model_calls: list[str] = []
    science = "# Results\n\nThe synthetic treatment increased the signal."

    async def reader(_reference: str) -> str:
        """Return synthetic scientific input without storage access."""
        assert scenario != "no_text"
        return "The synthetic treatment increased the signal."

    async def summarizer(prompt: str) -> str:
        """Model only the external synthesis outcome."""
        model_calls.append(prompt)
        assert scenario != "no_text"
        if scenario == "timeout":
            raise TimeoutError("private-provider /tmp/private-fixture")
        if scenario == "error":
            raise RuntimeError("private-provider task-fixture")
        if scenario == "empty":
            return " \n "
        if scenario == "operational":
            return "Execution log: /tmp/private-fixture task-fixture"
        return science

    assembly = await report_mod.assemble_terminal_report(
        reader=reader,
        summarizer=summarizer,
        context=context,
        artifacts=context.artifacts,
    )
    result = await synthesize_terminal_report(
        context, reader=reader, summarizer=summarizer
    )
    manager = TaskManager(str(tmp_path / "tasks.sqlite"))
    manager.record(
        Submission(task_id="task-fixture", status="succeeded", output_dir="")
    )
    persist_terminal_report(context.live, result, task_manager=manager)
    persisted = manager.get_task("task-fixture")
    assert persisted is not None
    expected = science if scenario == "valid" else ""
    assert assembly.answer == result.final_report == expected
    assert manager.get_task_final_report("task-fixture") == expected
    assert context.live[0]["final_report"] == expected
    assert persisted["status"] == context.live[0]["status"] == "succeeded"
    assert len(model_calls) == (0 if scenario == "no_text" else 2)
    if scenario != "valid":
        assert result.answer == ""
    assert result.degraded is (scenario != "valid")
    assert asdict(assembly.report) == {
        "state": "final" if scenario == "valid" else "degraded",
        "degraded": scenario != "valid",
        "source_artifact_count": len(context.artifacts),
    }
    code = (
        "report_no_scientific_text"
        if scenario == "no_text"
        else "report_synthesis_failed"
    )
    assert [warning.code for warning in assembly.warnings] == (
        [] if scenario == "valid" else [code]
    )
    if scenario != "valid":
        assert result.degraded_reason == code
        assert manager.get_task_degraded("task-fixture") == code
        assert context.live[0]["degraded"]
    assert "private-provider" not in str(asdict(assembly))
    assert "/tmp/private-fixture" not in str(asdict(assembly))
    persist_report_compatibility(context.live, assembly, manager.db_path)
    persisted = manager.get_task("task-fixture")
    assert persisted is not None
    assert manager.get_task_final_report("task-fixture") == expected


def test_persist_terminal_report_records_storage_failure() -> None:
    """A TaskManager OSError degrades the live row without leaking details."""

    class _BoomManager:
        def set_task_final_report(self, *_args: object) -> bool:
            """Fail persistence so the live row is marked degraded."""
            raise OSError("disk")

        def set_task_degraded(self, *_args: object) -> bool:
            """Record the degraded reason on the live row."""
            return True

    live = [{"task_id": "task-1", "status": "succeeded"}]
    persist_terminal_report(
        live,
        TerminalReportResult(
            final_report="report",
            answer="answer",
            degraded=True,
            degraded_reason="report_synthesis_failed",
        ),
        task_manager=_BoomManager(),
    )
    assert live[0]["degraded_reason"] == (
        "terminal report persistence failed: OSError"
    )


def test_bounded_utf8_text_applies_byte_and_char_caps() -> None:
    """UTF-8 byte truncation and leftover character caps are both applied."""
    text, truncated = getattr(report_mod, "_bounded_utf8_text")(
        "éééé", max_bytes=3, max_chars=10
    )
    assert truncated
    text, truncated = getattr(report_mod, "_bounded_utf8_text")(
        "abcdef", max_bytes=100, max_chars=3
    )
    assert truncated
    assert text == "abc"


def test_artifact_name_and_reference_fallbacks() -> None:
    """Name falls back to a generic label; missing refs raise OSError."""
    assert (
        getattr(report_mod, "_artifact_name")({"role": "scientific_report"})
        == "scientific-artifact"
    )
    with pytest.raises(OSError, match="artifact source reference unavailable"):
        getattr(report_mod, "_artifact_read_reference")(
            {"role": "scientific_report"}
        )
    assert (
        getattr(report_mod, "_artifact_read_reference")(
            {"download_ref": "owner/file.md"}
        )
        == "owner/file.md"
    )
