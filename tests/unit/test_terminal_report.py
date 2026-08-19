# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for terminal remote-run final report assembly."""

# pylint: disable=too-many-lines

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.runtime import terminal_report as report_mod
from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.locale import SupportedLocale
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportAssembly,
    TerminalReportContext,
    TerminalReportResult,
    TextArtifactSnippet,
    assemble_terminal_report,
    build_fallback_report,
    is_terminal_report_agent,
    persist_terminal_report,
    read_obs_text_artifact,
    read_text_artifact_snippets,
    select_text_artifact_paths,
    synthesize_terminal_report,
)

pytestmark = pytest.mark.unit


def _classified_artifact(
    name: str,
    role: ArtifactRole | str,
    *,
    size_bytes: int | None = None,
) -> ClassifiedArtifact:
    """Build a manifest-backed artifact with a fixture-only source ref."""
    return ClassifiedArtifact(
        source_path=f"fixture://{name}",
        relative_path=name,
        role=ArtifactRole(role),
        media_type="text/plain",
        size_bytes=size_bytes if size_bytes is not None else 32,
        download_ref=f"download://{name}",
    )


def test_terminal_report_agent_gate() -> None:
    """Analyst-class agents opt in; other agents stay out."""
    assert is_terminal_report_agent("analyst")
    assert is_terminal_report_agent("research")
    assert is_terminal_report_agent("design")
    assert is_terminal_report_agent("network")
    assert not is_terminal_report_agent("deep_genome")
    assert not is_terminal_report_agent("chat")


def test_only_scientific_text_roles_are_report_context_eligible() -> None:
    """Only the three explicit scientific roles may enter synthesis."""
    eligible = {
        role.value
        for role in ArtifactRole
        if _classified_artifact("fixture.txt", role).report_context_eligible
    }

    assert eligible == {
        "scientific_report",
        "scientific_table",
        "scientific_text",
    }

    assert not _classified_artifact(
        "normalized.parquet", ArtifactRole.SCIENTIFIC_DATA
    ).report_context_eligible


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


def test_build_fallback_report_excludes_operational_artifacts() -> None:
    """Fallback renders outcome metadata without paths or artifact content."""
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
    assert "/obs/bucket/run-a" not in result.final_report
    assert "report.md" not in result.final_report
    assert "network.svg" not in result.final_report
    assert result.answer == "Analysis complete: 2/2 tasks succeeded."


def test_build_fallback_report_handles_empty_artifacts() -> None:
    """Empty artifact list yields a healthy outcome report."""
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
    assert "output directories" not in result.final_report
    assert result.answer == "Analysis complete: 1/1 tasks succeeded."


def test_build_fallback_report_uses_context_locale_for_bot_prose() -> None:
    """Chinese fallback prose changes while paths stay out of the report."""
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
    assert "/obs/bucket/zh-run" not in result.final_report
    assert result.answer == "分析完成：1/1 个任务成功。"


async def _fake_reader(path: str) -> str:
    """Return canned text for the recognised test paths."""
    values = {
        "/obs/bucket/a.md": "alpha text",
        "/obs/bucket/b.csv": "bravo text",
        "/obs/bucket/large.txt": "x" * 20,
        "/obs/bucket/report.md": "report content",
        "fixture://report.md": "report content",
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
    assert "Scientific artifact: report.md" in prompt
    assert "/obs/bucket/report.md" not in prompt
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
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT)
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
    assert result.selected_paths == ()


async def test_synthesize_terminal_report_falls_back_on_empty_summary() -> (
    None
):
    """An empty summarizer response triggers the fallback report."""
    context = TerminalReportContext(
        agent="design",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT)
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
    assert result.degraded_reason == "report_synthesis_failed"
    assert "scientific report synthesis was unavailable" in result.final_report
    assert "/obs/" not in result.final_report


async def test_synthesize_report_falls_back_on_summary_exception() -> None:
    """A summarizer exception triggers the fallback report."""
    context = TerminalReportContext(
        agent="analyst",
        status="succeeded",
        live=[{"task_id": "task-1", "status": "succeeded"}],
        artifacts=[
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT)
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
    assert result.degraded_reason == "report_synthesis_failed"
    assert "scientific report synthesis was unavailable" in result.final_report
    assert "/obs/" not in result.final_report


def _report_context(
    locale: SupportedLocale = "en-US",
) -> TerminalReportContext:
    """Build a minimal context for canonical assembly tests."""
    return TerminalReportContext(
        agent="research",
        status="succeeded",
        live=[{"task_id": "task-sentinel", "status": "succeeded"}],
        artifacts=[],
        query="compare candidate genes",
        locale=locale,
    )


@pytest.mark.asyncio
async def test_only_explicit_scientific_text_enters_prompt() -> None:
    """Only producer-declared scientific text roles reach the summarizer."""
    artifacts = (
        _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT),
        _classified_artifact("table.csv", ArtifactRole.SCIENTIFIC_TABLE),
        _classified_artifact("notes.txt", ArtifactRole.SCIENTIFIC_TEXT),
        _classified_artifact("figure.png", ArtifactRole.SCIENTIFIC_FIGURE),
        _classified_artifact(
            "normalized.parquet", ArtifactRole.SCIENTIFIC_DATA
        ),
        _classified_artifact("analysis.log", ArtifactRole.EXECUTION_LOG),
        _classified_artifact("diag.json", ArtifactRole.DIAGNOSTIC),
        _classified_artifact("input.csv", ArtifactRole.INPUT),
        _classified_artifact("unknown.txt", ArtifactRole.UNKNOWN),
    )
    content = {
        "fixture://report.md": "RESULT-SENTINEL",
        "fixture://table.csv": "TABLE-SENTINEL",
        "fixture://notes.txt": "TEXT-SENTINEL",
        "fixture://figure.png": "FIGURE-SENTINEL",
        "fixture://normalized.parquet": "DATA-SENTINEL",
        "fixture://analysis.log": "LOG-SENTINEL",
        "fixture://diag.json": "DIAG-SENTINEL",
        "fixture://input.csv": "INPUT-SENTINEL",
        "fixture://unknown.txt": "UNKNOWN-SENTINEL",
    }
    captured: dict[str, str] = {}

    async def reader(reference: str) -> str:
        """Return fixture content for whichever role is admitted."""
        return content[reference]

    async def summarizer(prompt: str) -> str:
        """Capture the path-free prompt and return a report."""
        captured["prompt"] = prompt
        return "Results\n\nMethods\n\nLimitations\n\nScientific context"

    result = await assemble_terminal_report(
        context=_report_context(),
        artifacts=artifacts,
        reader=reader,
        summarizer=summarizer,
    )

    assert isinstance(result, TerminalReportAssembly)
    prompt = captured["prompt"]
    for included in ("RESULT-SENTINEL", "TABLE-SENTINEL", "TEXT-SENTINEL"):
        assert included in prompt
    for excluded in (
        "FIGURE-SENTINEL",
        "DATA-SENTINEL",
        "LOG-SENTINEL",
        "DIAG-SENTINEL",
        "INPUT-SENTINEL",
        "UNKNOWN-SENTINEL",
        "analysis.log",
    ):
        assert excluded not in prompt
    assert result.report.state == "final"
    assert result.report.source_artifact_count == 3


@pytest.mark.asyncio
async def test_no_scientific_text_returns_safe_degraded_report() -> None:
    """Execution logs cannot be promoted when no scientific role exists."""

    async def reader(_reference: str) -> str:
        """Fail if an excluded artifact is ever read."""
        raise AssertionError("excluded artifact was read")

    result = await assemble_terminal_report(
        context=_report_context("zh-CN"),
        artifacts=(
            _classified_artifact("analysis.log", ArtifactRole.EXECUTION_LOG),
        ),
        reader=reader,
    )

    assert result.answer.strip()
    assert "analysis.log" not in result.answer
    assert "secret" not in result.answer
    assert result.report.state == "degraded"
    assert result.report.source_artifact_count == 0
    assert result.warnings[0].code == "report_no_scientific_text"


@pytest.mark.asyncio
async def test_scientific_data_plain_text_becomes_official_answer() -> None:
    """A line-count txt declared as scientific_data becomes the official
    body."""

    async def reader(reference: str) -> str:
        """Return the producer conclusion file."""
        assert reference == "fixture://line_count.txt"
        return "4\n"

    async def summarizer(_prompt: str) -> str:
        raise AssertionError(
            "tiny conclusions must not require chat synthesis"
        )

    result = await assemble_terminal_report(
        context=_report_context("zh-CN"),
        artifacts=(
            _classified_artifact(
                "line_count.txt", ArtifactRole.SCIENTIFIC_DATA, size_bytes=2
            ),
            _classified_artifact("analysis.log", ArtifactRole.EXECUTION_LOG),
        ),
        reader=reader,
        summarizer=summarizer,
    )

    assert "4" in result.answer
    assert "line_count.txt" in result.answer
    assert "分析已到达终态" not in result.answer
    assert result.report.state == "final"
    assert result.report.source_artifact_count == 1


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en-US", "scientific report synthesis was unavailable"),
        ("zh-CN", "科学报告综合不可用"),
    ],
)
@pytest.mark.asyncio
async def test_report_synthesis_failure_is_locale_consistent(
    locale: SupportedLocale,
    expected: str,
) -> None:
    """Timeouts degrade without copying private paths or task identifiers."""

    async def reader(_reference: str) -> str:
        """Return one admitted scientific section."""
        return "validated scientific result"

    async def summarizer(_prompt: str) -> str:
        """Simulate an unavailable report model."""
        raise TimeoutError("provider-private-path run-sentinel")

    result = await assemble_terminal_report(
        context=_report_context(locale),
        artifacts=(
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT),
        ),
        reader=reader,
        summarizer=summarizer,
    )

    assert expected in result.answer
    assert "provider-private-path" not in result.answer
    assert "run-sentinel" not in result.answer
    assert result.report.state == "degraded"
    assert result.report.source_artifact_count == 1
    assert any(
        warning.code == "report_synthesis_failed"
        for warning in result.warnings
    )


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


def test_select_text_artifact_paths_skips_malformed_entries() -> None:
    """Non-list path bags and non-string items are ignored without a cap."""
    assert select_text_artifact_paths(
        [{"paths": "not-a-list"}, {"paths": [None, 1, "notes.txt"]}]
    ) == ["notes.txt"]


def test_localize_report_reason_covers_known_and_unknown_zh() -> None:
    """Chinese fallback translates known assembler reasons only."""
    assert (
        getattr(report_mod, "_localize_report_reason")("plain", "en-US")
        == "plain"
    )
    assert (
        getattr(report_mod, "_localize_report_reason")(
            "No readable text artifacts were available for LLM summary",
            "zh-CN",
        )
        == "没有可供 LLM 总结的可读文本工件。"
    )
    assert (
        getattr(report_mod, "_localize_report_reason")(
            "LLM summary failed: timeout", "zh-CN"
        )
        == "LLM 总结失败：timeout"
    )
    assert (
        getattr(report_mod, "_localize_report_reason")(
            "custom reason", "zh-CN"
        )
        == "custom reason"
    )


def test_all_artifact_paths_flattens_string_entries() -> None:
    """Legacy path flattening skips non-list bags and non-string items."""
    assert getattr(report_mod, "_all_artifact_paths")(
        [{"paths": "bad"}, {"paths": ["ok.md", 1]}]
    ) == ["ok.md"]


async def test_read_text_artifact_snippets_skips_when_budget_exhausted() -> (
    None
):
    """A zero remaining budget records later paths as skipped."""
    snippets, skipped = await read_text_artifact_snippets(
        ["/obs/bucket/a.md", "/obs/bucket/b.csv"],
        reader=_fake_reader,
        max_total_chars=0,
    )
    assert snippets == ()
    assert skipped == ("/obs/bucket/a.md", "/obs/bucket/b.csv")


async def test_read_text_artifact_snippets_trims_to_remaining_chars() -> None:
    """The leftover character budget trims a later snippet and then skips."""
    snippets, skipped = await read_text_artifact_snippets(
        ["/obs/bucket/a.md", "/obs/bucket/large.txt"],
        reader=_fake_reader,
        max_bytes_per_artifact=20,
        max_total_chars=8,
    )
    assert [item.content for item in snippets] == ["alpha te"]
    assert skipped == ("/obs/bucket/large.txt",)


async def test_read_obs_text_artifact_reads_downloaded_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS text reads download into the report temp dir then decode UTF-8."""
    local = tmp_path / "notes.txt"
    local.write_text("obs text", encoding="utf-8")

    async def fake_download(_path: str, dest_dir: str) -> str:
        assert dest_dir == "terminal-report"
        return str(local)

    monkeypatch.setattr(report_mod, "download_obs_file", fake_download)
    assert await read_obs_text_artifact("owner/notes.txt") == "obs text"


@pytest.mark.asyncio
async def test_mapping_artifacts_and_admission_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mapping artifacts exercise role, size, name, and admission warnings."""
    monkeypatch.setattr(report_mod, "_MAX_TEXT_ARTIFACTS", 1)
    monkeypatch.setattr(report_mod, "_MAX_TOTAL_PROMPT_CHARS", 8)
    captured: dict[str, str] = {}

    async def reader(reference: str) -> str:
        return {
            "fixture://keep.md": "ABCDEFGH extra",
            "fixture://skip.md": "should-not-read",
        }[reference]

    async def summarizer(prompt: str) -> str:
        captured["prompt"] = prompt
        return "Results\nMethods\nLimitations\nScientific context"

    result = await assemble_terminal_report(
        context=_report_context(),
        artifacts=(
            {
                "role": "scientific_report",
                "report_context_eligible": True,
                "name": "keep.md",
                "size_bytes": 12,
                "source_path": "fixture://keep.md",
            },
            {
                "role": "scientific_text",
                "relative_path": "skip.md",
                "source_path": "fixture://skip.md",
            },
            {
                "role": "scientific_report",
                "report_context_eligible": False,
                "path": "blocked.md",
            },
            {"role": "not-a-role", "path": "bad.md"},
            {"path": "no-role.md"},
            {
                "role": "scientific_table",
                "size_bytes": True,
                "name": "bool-size.csv",
            },
            {"role": "scientific_table", "size_bytes": -3, "name": "neg.csv"},
        ),
        reader=reader,
        summarizer=summarizer,
    )
    assert result.report.state == "final"
    assert any(
        warning.code == "report_artifact_count_capped"
        for warning in result.warnings
    )
    assert "ABCDEFGH" in captured["prompt"]


@pytest.mark.asyncio
async def test_admission_handles_size_read_and_empty_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Size, read, empty, and remaining-budget warnings stay sanitized."""
    monkeypatch.setattr(report_mod, "_MAX_BYTES_PER_ARTIFACT", 8)
    monkeypatch.setattr(report_mod, "_MAX_TOTAL_PROMPT_CHARS", 8)

    async def reader(reference: str) -> str:
        if reference.endswith("missing.md"):
            raise OSError("missing")
        if reference.endswith("empty.md"):
            return "   "
        if reference.endswith("bad-type.md"):
            return cast(str, 1)
        return "12345678 extra"

    result = await assemble_terminal_report(
        context=_report_context(),
        artifacts=(
            _classified_artifact(
                "huge.md", ArtifactRole.SCIENTIFIC_REPORT, size_bytes=99
            ),
            _classified_artifact(
                "missing.md", ArtifactRole.SCIENTIFIC_TEXT, size_bytes=4
            ),
            _classified_artifact(
                "empty.md", ArtifactRole.SCIENTIFIC_TABLE, size_bytes=4
            ),
            _classified_artifact(
                "bad-type.md", ArtifactRole.SCIENTIFIC_TEXT, size_bytes=4
            ),
            _classified_artifact(
                "ok.md", ArtifactRole.SCIENTIFIC_REPORT, size_bytes=8
            ),
            _classified_artifact(
                "later.md", ArtifactRole.SCIENTIFIC_TEXT, size_bytes=4
            ),
        ),
        reader=reader,
        summarizer=_empty_summarizer,
    )
    codes = {warning.code for warning in result.warnings}
    assert "report_artifact_size_exceeded" in codes
    assert "report_artifact_read_failed" in codes
    assert "report_artifact_empty" in codes
    assert "report_context_truncated" in codes


@pytest.mark.asyncio
async def test_default_reader_uses_local_then_obs(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default reader prefers a local file and otherwise downloads."""
    local = tmp_path / "report.md"
    local.write_text("local body", encoding="utf-8")

    async def fake_obs(reference: str) -> str:
        assert reference == "owner/remote.md"
        return "remote body"

    monkeypatch.setattr(report_mod, "read_obs_text_artifact", fake_obs)
    assert (
        await getattr(report_mod, "_read_report_artifact")(str(local))
        == "local body"
    )
    assert (
        await getattr(report_mod, "_read_report_artifact")("owner/remote.md")
        == "remote body"
    )


@pytest.mark.asyncio
async def test_default_summarizer_and_non_string_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting a summarizer uses chat, and a non-string result degrades."""

    async def fake_chat(prompt: str, _locale: str = "en-US") -> str:
        assert "Scientific artifact: report.md" in prompt
        return "Results\nMethods\nLimitations\nScientific context"

    monkeypatch.setattr(report_mod, "_summarize_with_chat", fake_chat)
    result = await assemble_terminal_report(
        context=_report_context(),
        artifacts=(
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT),
        ),
        reader=_fake_reader,
    )
    assert result.report.state == "final"

    async def not_text(_prompt: str) -> object:
        return 123

    failed = await assemble_terminal_report(
        context=_report_context(),
        artifacts=(
            _classified_artifact("report.md", ArtifactRole.SCIENTIFIC_REPORT),
        ),
        reader=_fake_reader,
        summarizer=cast(Any, not_text),
    )
    assert failed.report.state == "degraded"


@pytest.mark.asyncio
async def test_generated_report_rejects_operational_echo() -> None:
    """Model output that echoes paths, markers, or live IDs is discarded."""

    async def reader(_reference: str) -> str:
        return "validated scientific result"

    async def path_report(_prompt: str) -> str:
        return "see /obs/bucket/report.md"

    async def marker_report(_prompt: str) -> str:
        return "task id: leaked"

    async def live_id_report(_prompt: str) -> str:
        return "task-sentinel is complete"

    for summarizer in (path_report, marker_report, live_id_report):
        result = await assemble_terminal_report(
            context=_report_context(),
            artifacts=(
                _classified_artifact(
                    "report.md", ArtifactRole.SCIENTIFIC_REPORT
                ),
            ),
            reader=reader,
            summarizer=summarizer,
        )
        assert result.report.state == "degraded"

    async def _secret_summarizer(_prompt: str) -> str:
        return "mentions secret-source-path"

    mapping_result = await assemble_terminal_report(
        context=_report_context(),
        artifacts=(
            {
                "role": "scientific_report",
                "source_path": "secret-source-path",
                "name": "report.md",
            },
        ),
        reader=reader,
        summarizer=_secret_summarizer,
    )
    assert mapping_result.report.state == "degraded"


def test_build_report_prompt_includes_legacy_and_truncated_snippets() -> None:
    """Legacy path snippets and truncation notes stay in the prompt."""
    prompt = getattr(report_mod, "_build_report_prompt")(
        _report_context(),
        (
            TextArtifactSnippet(
                path="/obs/bucket/notes.txt",
                content="legacy body",
                truncated=True,
            ),
        ),
    )
    assert "Artifact: /obs/bucket/notes.txt" in prompt
    assert "Snippet was truncated" in prompt


async def test_summarize_with_chat_uses_chat_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default chat summarizer unwraps the chat app response."""

    class _FakeApp:
        async def ainvoke(self, payload: object) -> object:
            """Return a canned chat-app payload."""
            assert payload == {"prompt": "ok"}
            return {"messages": ["chat"]}

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_FakeApp"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    monkeypatch.setattr(report_mod, "ChatConfig", object)
    monkeypatch.setattr(
        report_mod,
        "SensitiveConfig",
        type("Cfg", (), {"load": staticmethod(object)}),
    )
    monkeypatch.setattr(
        report_mod, "build_chat_kwargs_for", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        report_mod, "build_chat_input", lambda **_k: {"prompt": "ok"}
    )
    monkeypatch.setattr(report_mod, "_cached_chat_app", _FakeApp)
    monkeypatch.setattr(
        report_mod, "extract_chat_response", lambda output: output
    )
    monkeypatch.setattr(
        report_mod, "message_content", lambda message: "chat report"
    )
    assert await getattr(report_mod, "_summarize_with_chat")(
        "ok", "en-US"
    ) == ("chat report")


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
