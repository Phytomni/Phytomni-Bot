# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for terminal report assembly helpers."""

# pylint: disable=protected-access

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.runtime import terminal_report as report_mod
from mcp_server_phytomni.runtime.artifact_roles import (
    ArtifactRole,
    ClassifiedArtifact,
)
from mcp_server_phytomni.runtime.locale import SupportedLocale
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportContext,
    TerminalReportResult,
    TextArtifactSnippet,
    _admit_report_artifacts,
    _all_artifact_paths,
    _artifact_is_report_eligible,
    _artifact_name,
    _artifact_read_reference,
    _artifact_role,
    _artifact_size_bytes,
    _bounded_utf8_text,
    _build_report_prompt,
    _generated_report_contains_operational_data,
    _localize_report_reason,
    _read_report_artifact,
    _ReportArtifactSnippet,
    assemble_terminal_report,
    persist_terminal_report,
    read_obs_text_artifact,
    read_text_artifact_snippets,
    select_text_artifact_paths,
    synthesize_terminal_report,
)

pytestmark = pytest.mark.unit


def _context(
    locale: SupportedLocale = "en-US",
    **overrides: Any,
) -> TerminalReportContext:
    """Build a minimal report context with optional field overrides."""
    values: dict[str, Any] = {
        "agent": "research",
        "status": "succeeded",
        "live": [{"task_id": "task-1", "status": "succeeded"}],
        "artifacts": [],
        "query": "compare cultivars",
        "locale": locale,
    }
    values.update(overrides)
    return TerminalReportContext(**values)


def _classified(
    name: str,
    role: ArtifactRole = ArtifactRole.SCIENTIFIC_REPORT,
    **overrides: Any,
) -> ClassifiedArtifact:
    """Build one classified artifact with optional field overrides."""
    values: dict[str, Any] = {
        "source_path": f"fixture://{name}",
        "relative_path": name,
        "role": role,
        "media_type": "text/plain",
        "size_bytes": 32,
        "download_ref": f"download://{name}",
    }
    values.update(overrides)
    return ClassifiedArtifact(**values)


def test_select_text_paths_skips_non_list_and_non_str() -> None:
    """Malformed path collections are ignored rather than crashing."""
    selected = select_text_artifact_paths(
        [
            {"paths": "not-a-list"},
            {"paths": [None, 12, "/obs/run/notes.txt"]},
        ]
    )
    assert selected == ["/obs/run/notes.txt"]


def test_select_text_paths_returns_when_cap_reached() -> None:
    """The selector stops as soon as the file cap is filled."""
    selected = select_text_artifact_paths(
        [{"paths": ["/a.md", "/b.txt", "/c.csv", "/d.log"]}],
        max_files=2,
    )
    assert selected == ["/a.md", "/b.txt"]


def test_localize_report_reason_covers_known_and_unknown() -> None:
    """Known English reasons translate; unknown text is kept."""
    empty = "LLM summary returned empty content"
    missing = "No readable text artifacts were available for LLM summary"
    assert _localize_report_reason(empty, "en-US") == empty
    assert _localize_report_reason(empty, "zh-CN") == "LLM 总结返回了空内容。"
    assert _localize_report_reason(missing, "zh-CN") == (
        "没有可供 LLM 总结的可读文本工件。"
    )
    assert _localize_report_reason("LLM summary failed: timeout", "zh-CN") == (
        "LLM 总结失败：timeout"
    )
    assert _localize_report_reason("custom reason", "zh-CN") == "custom reason"


def test_all_artifact_paths_flattens_string_entries() -> None:
    """Only string paths survive the compatibility flatten helper."""
    paths = _all_artifact_paths(
        [
            {"paths": "skip"},
            {"paths": ["/a.md", None, "/b.csv"]},
        ]
    )
    assert paths == ["/a.md", "/b.csv"]


async def test_read_snippets_skips_when_budget_exhausted() -> None:
    """A zero remaining budget records later paths as skipped."""

    async def _reader(path: str) -> str:
        return path

    snippets, skipped = await read_text_artifact_snippets(
        ["/a.md", "/b.md"],
        reader=_reader,
        max_total_chars=0,
    )
    assert snippets == ()
    assert skipped == ("/a.md", "/b.md")


async def test_read_snippets_clips_to_remaining_chars() -> None:
    """A later snippet is clipped to the leftover character budget."""

    async def _reader(_path: str) -> str:
        return "abcdef"

    snippets, skipped = await read_text_artifact_snippets(
        ["/a.md", "/b.md"],
        reader=_reader,
        max_bytes_per_artifact=8,
        max_total_chars=8,
    )
    assert skipped == ()
    assert snippets[0].content == "abcdef"
    assert snippets[1].content == "ab"
    assert snippets[1].truncated is True


async def test_read_obs_text_artifact_decodes_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS-backed reads decode the downloaded file as UTF-8."""
    local = tmp_path / "report.md"
    local.write_text("obs-body", encoding="utf-8")

    async def _download(path: str, temp_dir: str) -> str:
        assert path == "obs://report.md"
        assert temp_dir == report_mod._REPORT_TEMP_DIR
        return str(local)

    monkeypatch.setattr(report_mod, "download_obs_file", _download)
    assert await read_obs_text_artifact("obs://report.md") == "obs-body"


def test_mapping_artifact_helpers_cover_invalid_roles() -> None:
    """Mapping descriptors keep invalid roles and sizes out of context."""
    mapping = {
        "role": "scientific_report",
        "name": "notes.md",
        "size_bytes": 12,
        "source_path": "/tmp/notes.md",
        "report_context_eligible": True,
    }
    assert _artifact_role(mapping) is ArtifactRole.SCIENTIFIC_REPORT
    assert _artifact_is_report_eligible(mapping) is True
    assert _artifact_size_bytes(mapping) == 12
    assert _artifact_name(mapping) == "notes.md"
    assert _artifact_read_reference(mapping) == "/tmp/notes.md"

    assert _artifact_role({"role": 1}) is None
    assert _artifact_role({"role": "not-a-role"}) is None
    assert (
        _artifact_is_report_eligible(
            {"role": "scientific_report", "report_context_eligible": False}
        )
        is False
    )
    assert _artifact_size_bytes({"size_bytes": True}) is None
    assert _artifact_size_bytes({"size_bytes": -3}) is None
    assert (
        _artifact_name({"relative_path": "tables\\genes.csv"}) == "genes.csv"
    )
    assert _artifact_name({}) == "scientific-artifact"
    with pytest.raises(OSError, match="source reference unavailable"):
        _artifact_read_reference({"path": ""})


async def test_read_report_artifact_prefers_local_file(
    tmp_path: Path,
) -> None:
    """A local source path is read without falling back to OBS."""
    local = tmp_path / "local.md"
    local.write_text("local-body", encoding="utf-8")
    assert await _read_report_artifact(str(local)) == "local-body"


async def test_read_report_artifact_falls_back_to_obs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing local path uses the OBS text reader."""

    async def _obs(reference: str) -> str:
        return f"remote:{reference}"

    monkeypatch.setattr(report_mod, "read_obs_text_artifact", _obs)
    assert await _read_report_artifact("/missing/report.md") == (
        "remote:/missing/report.md"
    )


def test_bounded_utf8_text_applies_byte_and_char_caps() -> None:
    """UTF-8 byte caps and leftover character caps both truncate."""
    text, truncated = _bounded_utf8_text(
        "abcdef",
        max_bytes=3,
        max_chars=10,
    )
    assert text == "abc"
    assert truncated is True
    clipped, also_truncated = _bounded_utf8_text(
        "abcdef",
        max_bytes=32,
        max_chars=2,
    )
    assert clipped == "ab"
    assert also_truncated is True


async def test_admit_report_artifacts_covers_cap_and_failures() -> None:
    """Admission warns on count, size, empty, failed, and truncated reads."""
    artifacts: list[dict[str, Any]] = [
        {
            "role": "scientific_report",
            "name": "huge.md",
            "source_path": "/tmp/huge.md",
            "size_bytes": report_mod._MAX_BYTES_PER_ARTIFACT + 1,
        },
        {
            "role": "scientific_table",
            "name": "empty.md",
            "source_path": "/tmp/empty.md",
            "size_bytes": 4,
        },
        {
            "role": "scientific_report",
            "name": "missing.md",
            "source_path": "/tmp/missing.md",
            "size_bytes": 4,
        },
        {
            "role": "scientific_report",
            "name": "not-text.md",
            "source_path": "/tmp/not-text.md",
            "size_bytes": 4,
        },
        {
            "role": "scientific_report",
            "name": "long.md",
            "source_path": "/tmp/long.md",
            "size_bytes": 4,
        },
        *[
            {
                "role": "scientific_report",
                "name": f"part-{index}.md",
                "source_path": f"/tmp/part-{index}.md",
                "size_bytes": 8,
            }
            for index in range(report_mod._MAX_TEXT_ARTIFACTS)
        ],
    ]

    async def _reader(reference: str) -> Any:
        if reference.endswith("empty.md"):
            return "   "
        if reference.endswith("missing.md"):
            raise OSError("gone")
        if reference.endswith("not-text.md"):
            return 12
        if reference.endswith("long.md"):
            return "x" * (report_mod._MAX_BYTES_PER_ARTIFACT + 8)
        return "ok"

    snippets, warnings = await _admit_report_artifacts(
        artifacts, reader=_reader
    )
    codes = {warning.code for warning in warnings}
    assert "report_artifact_count_capped" in codes
    assert "report_artifact_size_exceeded" in codes
    assert "report_artifact_empty" in codes
    assert "report_artifact_read_failed" in codes
    assert "report_context_truncated" in codes
    assert any(item.name.startswith("part-") for item in snippets)


async def test_admit_report_artifacts_truncates_remaining_budget() -> None:
    """A later eligible artifact is dropped once the prompt budget is gone."""
    artifacts = [
        {
            "role": "scientific_report",
            "name": f"part-{index}.md",
            "source_path": f"/tmp/part-{index}.md",
            "size_bytes": 4,
        }
        for index in range(5)
    ]

    async def _reader(_reference: str) -> str:
        return "y" * report_mod._MAX_TOTAL_PROMPT_CHARS

    snippets, warnings = await _admit_report_artifacts(
        artifacts, reader=_reader
    )
    assert len(snippets) < 5
    assert any(
        warning.code == "report_context_truncated" for warning in warnings
    )


def test_generated_report_rejects_operational_markers() -> None:
    """Private paths, markers, and live identifiers stay out of prose."""
    context = _context(
        live=[
            {
                "task_id": "task-secret",
                "status": "succeeded",
                "provider": "acme",
            }
        ]
    )
    mapping = {
        "source_path": "/private/run/notes.md",
        "download_ref": "dl-ref-xyz",
        "output_dir": "/obs/owner/out",
    }
    assert _generated_report_contains_operational_data(
        context, (), "see /obs/bucket/run for details"
    )
    assert _generated_report_contains_operational_data(
        context, (), "Task ID: hidden"
    )
    assert _generated_report_contains_operational_data(
        context, (mapping,), "copied /private/run/notes.md"
    )
    assert not _generated_report_contains_operational_data(
        context, (mapping,), "cultivar comparison looks healthy"
    )


async def test_assemble_uses_default_summarizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting a summarizer still runs the locale-aware chat path."""

    async def _summary(prompt: str, locale: SupportedLocale = "en-US") -> str:
        assert "compare cultivars" in prompt
        assert locale == "zh-CN"
        return "Results\n\nMethods\n\nLimitations\n\nScientific context"

    monkeypatch.setattr(report_mod, "_summarize_with_chat", _summary)
    result = await assemble_terminal_report(
        context=_context("zh-CN"),
        artifacts=(_classified("report.md"),),
        reader=lambda _ref: _async_text("validated result"),
    )
    assert result.report.state == "final"
    assert "Results" in result.answer


async def _async_text(value: str) -> str:
    """Return an awaitable string for reader stubs."""
    return value


async def test_assemble_rejects_non_string_summary() -> None:
    """A non-text summarizer response degrades without leaking details."""

    async def _reader(_reference: str) -> str:
        return "validated result"

    async def _summarizer(_prompt: str) -> Any:
        return 12

    result = await assemble_terminal_report(
        context=_context(),
        artifacts=(_classified("report.md"),),
        reader=_reader,
        summarizer=_summarizer,
    )
    assert result.report.state == "degraded"
    assert any(
        warning.code == "report_synthesis_failed"
        for warning in result.warnings
    )


def test_build_prompt_renders_classified_and_legacy_snippets() -> None:
    """Prompt sections include role labels and truncation notes."""
    prompt = _build_report_prompt(
        _context(),
        (
            _ReportArtifactSnippet(
                name="report.md",
                role="scientific_report",
                content="body",
                truncated=True,
            ),
            TextArtifactSnippet(
                path="/obs/run/notes.txt",
                content="legacy",
                truncated=False,
            ),
        ),
    )
    assert "Scientific artifact: report.md" in prompt
    assert "Role: scientific_report" in prompt
    assert "Snippet was truncated by the report assembler." in prompt
    assert "Artifact: /obs/run/notes.txt" in prompt


async def test_summarize_with_chat_extracts_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default chat summarizer unwraps the chat-completion envelope."""

    async def ainvoke(_payload: Any) -> dict[str, Any]:
        """Ignore the prompt and return a chat-subgraph envelope."""
        return {
            "response": {"choices": [{"message": {"content": "chat-summary"}}]}
        }

    def _chat_app() -> Any:
        return SimpleNamespace(ainvoke=ainvoke)

    monkeypatch.setattr(report_mod, "_cached_chat_app", _chat_app)
    monkeypatch.setattr(
        report_mod,
        "build_chat_kwargs_for",
        lambda *_args, **_kwargs: {"locale": "en-US"},
    )
    text = await report_mod._summarize_with_chat("prompt", "en-US")
    assert text == "chat-summary"


def test_persist_terminal_report_records_persistence_failure() -> None:
    """SQLite failures stay on the live row without raising."""

    class _BoomManager:
        """Raise a durable error from the first persistence write."""

        def set_task_final_report(self, *_args: Any, **_kwargs: Any) -> None:
            """Fail the report write."""
            raise sqlite3.Error("locked")

        def set_task_degraded(self, *_args: Any, **_kwargs: Any) -> None:
            """Should not be reached after the first failure."""
            raise AssertionError("degraded write should not run")

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
    assert live[0]["final_report"] == "report"
    assert live[0]["degraded_reason"] == (
        "terminal report persistence failed: Error"
    )


async def test_synthesize_rejects_operational_summary() -> None:
    """A summary that echoes a task id degrades to the safe fallback."""

    async def _reader(_reference: str) -> str:
        return "validated result"

    async def _summarizer(_prompt: str) -> str:
        return "See task-1 in /obs/bucket/run"

    result = await synthesize_terminal_report(
        _context(artifacts=[_classified("report.md")]),
        reader=_reader,
        summarizer=_summarizer,
    )
    assert result.degraded is True
    assert result.degraded_reason == "report_synthesis_failed"
