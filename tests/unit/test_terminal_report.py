# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for terminal remote-run final report assembly."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportContext,
    TextArtifactSnippet,
    is_terminal_report_agent,
    select_text_artifact_paths,
)

pytestmark = pytest.mark.unit


def test_terminal_report_agent_gate() -> None:
    """Analyst-class agents opt in; other agents stay out."""
    assert is_terminal_report_agent("analyst")
    assert is_terminal_report_agent("research")
    assert is_terminal_report_agent("design")
    assert is_terminal_report_agent("network")
    assert not is_terminal_report_agent("deep_genome")
    assert not is_terminal_report_agent("chat")


def test_select_text_artifact_paths_filters_and_caps() -> None:
    """Text extensions pass; figures and archives are filtered; cap holds."""
    artifacts = [
        {
            "task_id": "task-1",
            "output_dir": "/obs/bucket/run-a",
            "paths": [
                "/obs/bucket/run-a/report.md",
                "/obs/bucket/run-a/table.csv",
                "/obs/bucket/run-a/figure.png",
                "/obs/bucket/run-a/data.JSON",
                "/obs/bucket/run-a/archive.zip",
            ],
        },
        {
            "task_id": "task-2",
            "output_dir": "/obs/bucket/run-b",
            "paths": ["/obs/bucket/run-b/notes.txt"],
        },
    ]

    selected = select_text_artifact_paths(artifacts, max_files=3)

    assert selected == [
        "/obs/bucket/run-a/report.md",
        "/obs/bucket/run-a/table.csv",
        "/obs/bucket/run-a/data.JSON",
    ]


def test_report_context_and_snippet_shapes() -> None:
    """Dataclass fields round-trip their construction values."""
    context = TerminalReportContext(
        agent="design",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            {
                "task_id": "task-1",
                "output_dir": "/obs/bucket/run-a",
                "paths": ["/obs/bucket/run-a/report.md"],
            }
        ],
        query="design an sgRNA workflow",
    )
    snippet = TextArtifactSnippet(
        path="/obs/bucket/run-a/report.md",
        content="analysis result",
        truncated=False,
    )

    assert context.agent == "design"
    assert context.query == "design an sgRNA workflow"
    assert snippet.path.endswith("report.md")
    assert not snippet.truncated
