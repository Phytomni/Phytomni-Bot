# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Scientific content survives terminal reporting without operational text."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportAssembly,
    TerminalReportContext,
    assemble_terminal_report,
    is_scientific_report_text,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "body",
    [
        "Analysis complete: promoter variants showed twofold enrichment "
        "(adjusted P=0.01).",
        "# Results\nThe failure analysis compared the labels "
        "'LLM summary failed:' and 'analysis complete:' across replicates.",
        "# Results\n**Analysis complete**\n\n"
        "Promoter variants showed twofold enrichment (adjusted P=0.01).",
        "Analysis complete: 1/1 tasks succeeded.\n\n"
        "Promoter variants showed twofold enrichment (adjusted P=0.01).",
    ],
)
def test_scientific_status_phrases_do_not_erase_science(body: str) -> None:
    """Known acknowledgement words are not whole-report classifiers."""
    context = TerminalReportContext(
        agent="design", status="succeeded", live=[], artifacts=(), query=None
    )
    assert is_scientific_report_text(context, (), body)


@pytest.mark.parametrize(
    "role", [ArtifactRole.SCIENTIFIC_DATA, ArtifactRole.UNKNOWN]
)
@pytest.mark.parametrize(
    "content",
    [
        "Execution log: analysis task failed.",
        "Provider: synthetic execution service",
        "Result unavailable at /tmp/synthetic-private-output",
        "Analysis complete: 1/1 tasks succeeded.",
        "LLM summary failed: TimeoutError",
        "The analysis reached a terminal outcome, but scientific report "
        "synthesis was unavailable.",
    ],
)
async def test_direct_conclusion_rejects_operational_output(
    role: ArtifactRole, content: str
) -> None:
    """Direct conclusions enforce the same boundary as model reports."""
    result = await _assemble_direct_conclusion(role, content)
    assert result.answer == ""
    assert result.report.state == "degraded"
    assert result.report.degraded
    assert result.report.source_artifact_count == 1
    assert [warning.code for warning in result.warnings] == [
        "report_synthesis_failed"
    ]


async def test_direct_conclusion_keeps_scientific_completion_prose() -> None:
    """Actual assembly preserves science that uses completion terminology."""
    content = (
        "Analysis complete: promoter variants showed twofold enrichment "
        "(adjusted P=0.01)."
    )
    result = await _assemble_direct_conclusion(
        ArtifactRole.SCIENTIFIC_DATA, content
    )
    assert result.answer.endswith(content)
    assert result.report.state == "final"
    assert result.report.degraded is False
    assert result.report.source_artifact_count == 1
    assert result.warnings == ()


async def _assemble_direct_conclusion(
    role: ArtifactRole, content: str
) -> TerminalReportAssembly:
    """Read a synthetic conclusion through the actual assembly boundary."""
    artifact = ClassifiedArtifact(
        source_path="fixture://conclusion.txt",
        relative_path="conclusion.txt",
        role=role,
        media_type="text/plain",
        size_bytes=128,
        download_ref="fixture:conclusion.txt",
    )

    async def reader(_reference: str) -> str:
        return content

    async def summarizer(_prompt: str) -> str:
        pytest.fail("A small direct conclusion must not invoke the model")

    return await assemble_terminal_report(
        context=TerminalReportContext(
            agent="design",
            status="succeeded",
            live=[{"task_id": "task-fixture", "status": "succeeded"}],
            artifacts=(artifact,),
            query=None,
        ),
        artifacts=(artifact,),
        reader=reader,
        summarizer=summarizer,
    )
