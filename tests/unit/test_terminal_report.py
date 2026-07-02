# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for terminal remote-run final report assembly."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportContext,
    TextArtifactSnippet,
    build_fallback_report,
    is_terminal_report_agent,
    read_text_artifact_snippets,
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


def test_build_fallback_report_includes_metadata_and_artifacts() -> None:
    """Fallback renders metadata, query, paths, and degraded reason."""
    context = TerminalReportContext(
        agent="network",
        status="succeeded",
        live=[
            {"task_id": "task-1", "status": "succeeded"},
            {"task_id": "task-2", "status": "succeeded"},
        ],
        artifacts=[
            {
                "task_id": "task-1",
                "output_dir": "/obs/bucket/run-a",
                "paths": [
                    "/obs/bucket/run-a/report.md",
                    "/obs/bucket/run-a/network.svg",
                ],
            }
        ],
        query="build a co-expression network",
    )

    result = build_fallback_report(
        context,
        reason="LLM summary unavailable",
        selected_paths=("/obs/bucket/run-a/report.md",),
        skipped_paths=("/obs/bucket/run-a/network.svg",),
    )

    assert result.degraded
    assert result.degraded_reason == "LLM summary unavailable"
    assert "# Network Analysis Final Report" in result.final_report
    assert "build a co-expression network" in result.final_report
    assert "2/2 tasks succeeded" in result.final_report
    assert "/obs/bucket/run-a/report.md" in result.final_report
    assert "/obs/bucket/run-a/network.svg" in result.final_report
    assert result.answer == "Analysis complete: 2/2 tasks succeeded."


def test_build_fallback_report_handles_empty_artifacts() -> None:
    """Empty artifact list yields a healthy report with no-output notice."""
    context = TerminalReportContext(
        agent="analyst",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[],
        query=None,
    )

    result = build_fallback_report(context)

    assert not result.degraded
    assert "# Analyst Final Report" in result.final_report
    assert "No output directories were reported." in result.final_report
    assert result.answer == "Analysis complete: 1/1 tasks succeeded."


async def _fake_reader(path: str) -> str:
    """Return canned text for the three recognised test paths."""
    values = {
        "/obs/bucket/a.md": "alpha text",
        "/obs/bucket/b.csv": "bravo text",
        "/obs/bucket/large.txt": "x" * 20,
    }
    return values[path]


async def _partly_failing_reader(path: str) -> str:
    """Raise for the sentinel missing path; return text otherwise."""
    if path.endswith("missing.md"):
        raise OSError("missing")
    return "readable"


async def test_read_text_artifact_snippets_applies_caps() -> None:
    """Per-artifact byte cap and total char cap are both enforced."""
    snippets, skipped = await read_text_artifact_snippets(
        ["/obs/bucket/a.md", "/obs/bucket/large.txt"],
        reader=_fake_reader,
        max_bytes_per_artifact=5,
        max_total_chars=12,
    )

    assert [snippet.path for snippet in snippets] == [
        "/obs/bucket/a.md",
        "/obs/bucket/large.txt",
    ]
    assert snippets[0].content == "alpha"
    assert snippets[0].truncated
    assert snippets[1].content == "xxxxx"
    assert snippets[1].truncated
    assert skipped == ()


async def test_read_text_artifact_snippets_records_failed_reads() -> None:
    """A read-time OSError lands the path on skipped, not snippets."""
    snippets, skipped = await read_text_artifact_snippets(
        ["/obs/bucket/ok.md", "/obs/bucket/missing.md"],
        reader=_partly_failing_reader,
    )

    assert [snippet.path for snippet in snippets] == ["/obs/bucket/ok.md"]
    assert skipped == ("/obs/bucket/missing.md",)
