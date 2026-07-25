# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for terminal remote-run final report assembly."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportContext,
    TerminalReportResult,
    TextArtifactSnippet,
    build_fallback_report,
    is_terminal_report_agent,
    persist_terminal_report,
    read_text_artifact_snippets,
    select_text_artifact_paths,
    synthesize_terminal_report,
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
        locale="en-US",
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
        locale="en-US",
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
        locale="en-US",
    )

    result = build_fallback_report(context)

    assert not result.degraded
    assert "# Analyst Final Report" in result.final_report
    assert "No output directories were reported." in result.final_report
    assert result.answer == "Analysis complete: 1/1 tasks succeeded."


def test_build_fallback_report_uses_context_locale_for_bot_prose() -> None:
    """Chinese fallback prose changes while paths and query stay exact."""
    context = TerminalReportContext(
        agent="analyst",
        status="succeeded",
        live=[{"task_id": "task-zh", "status": "succeeded"}],
        artifacts=[
            {
                "task_id": "task-zh",
                "output_dir": "/obs/bucket/zh-run",
                "paths": ["/obs/bucket/zh-run/report.md"],
            }
        ],
        query="分析 Os01g0177400",
        locale="zh-CN",
    )

    result = build_fallback_report(context)

    assert "# 分析智能体最终报告" in result.final_report
    assert "分析完成：1/1 个任务成功。" in result.final_report
    assert "分析 Os01g0177400" in result.final_report
    assert "/obs/bucket/zh-run/report.md" in result.final_report
    assert result.answer == "分析完成：1/1 个任务成功。"


async def _fake_reader(path: str) -> str:
    """Return canned text for the recognised test paths."""
    values = {
        "/obs/bucket/a.md": "alpha text",
        "/obs/bucket/b.csv": "bravo text",
        "/obs/bucket/large.txt": "x" * 20,
        "/obs/bucket/report.md": "report content",
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


async def _good_summarizer(prompt: str) -> str:
    """Return canned markdown when the prompt names the test artifact."""
    assert "Artifact: /obs/bucket/report.md" in prompt
    return "# LLM Report\n\nGrounded summary."


async def _empty_summarizer(prompt: str) -> str:
    """Return whitespace to exercise the empty-summary fallback."""
    assert prompt
    return "   "


async def _raising_summarizer(prompt: str) -> str:
    """Raise to exercise the exception fallback."""
    assert prompt
    raise OSError("model unavailable")


async def test_synthesize_terminal_report_uses_llm_report() -> None:
    """A healthy summarizer produces the primary report surface."""
    context = TerminalReportContext(
        agent="research",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            {
                "task_id": "task-1",
                "output_dir": "/obs/bucket/out",
                "paths": ["/obs/bucket/report.md"],
            }
        ],
        query="find candidate genes",
        locale="en-US",
    )

    result = await synthesize_terminal_report(
        context,
        reader=_fake_reader,
        summarizer=_good_summarizer,
    )

    assert result.final_report == "# LLM Report\n\nGrounded summary."
    assert result.answer == "Analysis complete: 1/1 tasks succeeded."
    assert not result.degraded
    assert result.selected_paths == ("/obs/bucket/report.md",)


async def test_synthesize_terminal_report_falls_back_on_empty_summary() -> (
    None
):
    """An empty summarizer response triggers the fallback report."""
    context = TerminalReportContext(
        agent="design",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            {
                "task_id": "task-1",
                "output_dir": "/obs/bucket/out",
                "paths": ["/obs/bucket/report.md"],
            }
        ],
        query="design primers",
        locale="en-US",
    )

    result = await synthesize_terminal_report(
        context,
        reader=_fake_reader,
        summarizer=_empty_summarizer,
    )

    assert result.degraded
    assert result.degraded_reason == "LLM summary returned empty content"
    assert "# Digital Design Final Report" in result.final_report


async def test_synthesize_report_falls_back_on_summary_exception() -> None:
    """A summarizer exception triggers the fallback report."""
    context = TerminalReportContext(
        agent="analyst",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            {
                "task_id": "task-1",
                "output_dir": "/obs/bucket/out",
                "paths": ["/obs/bucket/report.md"],
            }
        ],
        query="analyze dataset",
        locale="en-US",
    )

    result = await synthesize_terminal_report(
        context,
        reader=_fake_reader,
        summarizer=_raising_summarizer,
    )

    assert result.degraded
    assert result.degraded_reason == "LLM summary failed: OSError"
    assert "# Analyst Final Report" in result.final_report


class _FakeTaskManager:
    """Stand-in TaskManager capturing persistence calls."""

    def __init__(self) -> None:
        """Initialise empty capture lists."""
        self.final_reports: list[tuple[str, str]] = []
        self.degraded: list[tuple[str, str]] = []

    def set_task_final_report(self, task_id: str, markdown: str) -> bool:
        """Capture the final report write."""
        self.final_reports.append((task_id, markdown))
        return True

    def set_task_degraded(self, task_id: str, reason: str) -> bool:
        """Capture the degraded reason write."""
        self.degraded.append((task_id, reason))
        return True


def test_persist_terminal_report_updates_first_live_task() -> None:
    """Persistence patches the live row and calls both TaskManager methods."""
    live = [{"task_id": "task-1", "status": "succeeded"}]
    result = build_fallback_report(
        TerminalReportContext(
            agent="analyst",
            status="succeeded",
            live=live,
            artifacts=[],
            query=None,
            locale="en-US",
        ),
        reason="LLM summary returned empty content",
    )
    manager = _FakeTaskManager()

    persist_terminal_report(live, result, task_manager=manager)

    assert live[0]["final_report"] == result.final_report
    assert live[0]["degraded_reason"] == "LLM summary returned empty content"
    assert manager.final_reports == [("task-1", result.final_report)]
    assert manager.degraded == [
        ("task-1", "LLM summary returned empty content")
    ]


def test_persist_terminal_report_skips_missing_task_id() -> None:
    """A row without task_id is left untouched and no DB write fires."""
    live = [{"status": "succeeded"}]
    result = TerminalReportResult(final_report="report", answer="answer")
    manager = _FakeTaskManager()

    persist_terminal_report(live, result, task_manager=manager)

    assert "final_report" not in live[0]
    assert not manager.final_reports
