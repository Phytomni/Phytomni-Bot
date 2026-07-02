# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Assemble final reports for terminal analyst-class remote runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional

from ..agents.chat.service import _cached_chat_app
from ..common.responses import message_content
from ..config.defaults import ChatConfig
from ..config.settings import SensitiveConfig
from ..graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from ..storage.downloads import download_obs_file
from .terminal_answer import TerminalAnswerContext

__all__ = [
    "TerminalReportContext",
    "TerminalReportResult",
    "TextArtifactSnippet",
    "build_fallback_report",
    "is_terminal_report_agent",
    "read_obs_text_artifact",
    "read_text_artifact_snippets",
    "select_text_artifact_paths",
    "synthesize_terminal_report",
]

_TARGET_AGENTS = frozenset({"analyst", "research", "design", "network"})
_TEXT_EXTENSIONS = frozenset({".md", ".txt", ".json", ".csv", ".tsv", ".log"})
_FIGURE_EXTENSIONS = frozenset(
    {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}
)
_MAX_TEXT_ARTIFACTS = 8
_MAX_BYTES_PER_ARTIFACT = 32_768
_MAX_TOTAL_PROMPT_CHARS = 120_000
_SUMMARY_TIMEOUT_SECONDS = 90.0
_REPORT_TEMP_DIR = "terminal-report"


@dataclass(frozen=True)
class TerminalReportContext(TerminalAnswerContext):
    """Inputs required to build a terminal final report.

    Inherits the reconciled-run fields (agent, status, live,
    artifacts, query) from :class:`TerminalAnswerContext`; the
    report synthesizer reads the same shape plus any future
    report-only extensions added here.
    """


@dataclass(frozen=True)
class TextArtifactSnippet:
    """A capped text snippet read from one terminal artifact."""

    path: str
    content: str
    truncated: bool


@dataclass(frozen=True)
class TerminalReportResult:
    """Final report synthesis result."""

    final_report: str
    answer: str
    degraded: bool = False
    degraded_reason: Optional[str] = None
    selected_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()


def is_terminal_report_agent(agent: str) -> bool:
    """Return whether ``agent`` should receive terminal report synthesis."""

    return agent in _TARGET_AGENTS


def select_text_artifact_paths(
    artifacts: Iterable[Dict[str, Any]],
    *,
    max_files: int = _MAX_TEXT_ARTIFACTS,
) -> list[str]:
    """Return text-like artifact paths in stable artifact order."""

    selected: list[str] = []
    for artifact in artifacts:
        paths = artifact.get("paths", [])
        if not isinstance(paths, list):
            continue
        for path in paths:
            if not isinstance(path, str):
                continue
            suffix = PurePosixPath(path).suffix.lower()
            if suffix not in _TEXT_EXTENSIONS:
                continue
            selected.append(path)
            if len(selected) >= max_files:
                return selected
    return selected


def build_fallback_report(
    context: TerminalReportContext,
    *,
    reason: Optional[str] = None,
    selected_paths: tuple[str, ...] = (),
    skipped_paths: tuple[str, ...] = (),
) -> TerminalReportResult:
    """Build a deterministic non-empty report without LLM output."""

    succeeded = _success_count(context.live)
    total = len(context.live)
    title = _report_title(context.agent)
    answer = f"Analysis complete: {succeeded}/{total} tasks succeeded."
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        answer,
    ]
    if context.query:
        lines.extend(["", "## User Query", "", context.query])
    lines.extend(
        [
            "",
            "## Task Status",
            "",
            f"- Agent: `{context.agent}`",
            f"- Status: `{context.status}`",
            f"- Tasks: {succeeded}/{total} tasks succeeded",
        ]
    )
    output_dirs = [
        str(artifact.get("output_dir"))
        for artifact in context.artifacts
        if artifact.get("output_dir")
    ]
    lines.extend(["", "## Outputs", ""])
    if output_dirs:
        lines.extend(f"- `{output_dir}`" for output_dir in output_dirs)
    else:
        lines.append("No output directories were reported.")
    artifact_paths = _all_artifact_paths(context.artifacts)
    lines.extend(["", "## Artifacts", ""])
    if artifact_paths:
        lines.extend(f"- `{path}`" for path in artifact_paths)
    else:
        lines.append("No artifact files were reported.")
    if selected_paths:
        lines.extend(["", "## Text Artifacts Used", ""])
        lines.extend(f"- `{path}`" for path in selected_paths)
    if skipped_paths:
        lines.extend(["", "## Artifacts Not Summarized", ""])
        lines.extend(f"- `{path}`" for path in skipped_paths)
    degraded = bool(reason)
    if reason:
        lines.extend(["", "## Report Degradation", "", reason])
    final_report = "\n".join(lines).strip() + "\n"
    return TerminalReportResult(
        final_report=final_report,
        answer=answer,
        degraded=degraded,
        degraded_reason=reason,
        selected_paths=selected_paths,
        skipped_paths=skipped_paths,
    )


def _report_title(agent: str) -> str:
    """Return a human-readable final report heading for ``agent``."""
    titles = {
        "analyst": "Analyst Final Report",
        "research": "In Silico Research Final Report",
        "design": "Digital Design Final Report",
        "network": "Network Analysis Final Report",
    }
    return titles.get(agent, "Analysis Final Report")


def _success_count(live: Iterable[Dict[str, Any]]) -> int:
    """Count reconciled rows whose status is terminal-success."""
    return sum(
        1
        for row in live
        if str(row.get("status", "")).lower()
        in {"succeeded", "success", "completed", "done"}
    )


def _all_artifact_paths(artifacts: Iterable[Dict[str, Any]]) -> list[str]:
    """Flatten every path across all artifact descriptors."""
    paths: list[str] = []
    for artifact in artifacts:
        artifact_paths = artifact.get("paths", [])
        if not isinstance(artifact_paths, list):
            continue
        paths.extend(path for path in artifact_paths if isinstance(path, str))
    return paths


ArtifactTextReader = Callable[[str], Awaitable[str]]


async def read_text_artifact_snippets(
    paths: Iterable[str],
    *,
    reader: ArtifactTextReader,
    max_bytes_per_artifact: int = _MAX_BYTES_PER_ARTIFACT,
    max_total_chars: int = _MAX_TOTAL_PROMPT_CHARS,
) -> tuple[tuple[TextArtifactSnippet, ...], tuple[str, ...]]:
    """Read capped snippets from selected text artifact paths."""

    snippets: list[TextArtifactSnippet] = []
    skipped: list[str] = []
    remaining = max_total_chars
    for path in paths:
        if remaining <= 0:
            skipped.append(path)
            continue
        try:
            content = await reader(path)
        except (OSError, UnicodeDecodeError, ValueError):
            skipped.append(path)
            continue
        capped = content[:max_bytes_per_artifact]
        if len(capped) > remaining:
            capped = capped[:remaining]
        truncated = len(capped) < len(content)
        snippets.append(
            TextArtifactSnippet(
                path=path,
                content=capped,
                truncated=truncated,
            )
        )
        remaining -= len(capped)
    return tuple(snippets), tuple(skipped)


async def read_obs_text_artifact(path: str) -> str:
    """Resolve one OBS artifact path and read it as UTF-8 text."""

    local_path = await download_obs_file(path, _REPORT_TEMP_DIR)
    return await asyncio.to_thread(
        Path(local_path).read_text, encoding="utf-8"
    )


ReportSummarizer = Callable[[str], Awaitable[str]]


async def synthesize_terminal_report(
    context: TerminalReportContext,
    *,
    reader: ArtifactTextReader = read_obs_text_artifact,
    summarizer: Optional[ReportSummarizer] = None,
) -> TerminalReportResult:
    """Return an LLM-enhanced final report with deterministic fallback."""

    selected_paths = tuple(select_text_artifact_paths(context.artifacts))
    snippets, skipped_paths = await read_text_artifact_snippets(
        selected_paths,
        reader=reader,
    )
    all_paths = tuple(_all_artifact_paths(context.artifacts))
    skipped_all = tuple(
        path for path in all_paths if path not in selected_paths
    )
    combined_skipped = (*skipped_paths, *skipped_all)
    if not snippets:
        return build_fallback_report(
            context,
            reason="No readable text artifacts were available for LLM summary",
            selected_paths=selected_paths,
            skipped_paths=combined_skipped,
        )
    prompt = _build_report_prompt(context, snippets)
    use_summarizer = summarizer or _summarize_with_chat
    try:
        report = await asyncio.wait_for(
            use_summarizer(prompt),
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        return build_fallback_report(
            context,
            reason=f"LLM summary failed: {type(exc).__name__}",
            selected_paths=selected_paths,
            skipped_paths=combined_skipped,
        )
    report = report.strip()
    if not report:
        return build_fallback_report(
            context,
            reason="LLM summary returned empty content",
            selected_paths=selected_paths,
            skipped_paths=combined_skipped,
        )
    return TerminalReportResult(
        final_report=report,
        answer=(
            f"Analysis complete: {_success_count(context.live)}/"
            f"{len(context.live)} tasks succeeded."
        ),
        selected_paths=selected_paths,
        skipped_paths=combined_skipped,
    )


def _build_report_prompt(
    context: TerminalReportContext,
    snippets: Iterable[TextArtifactSnippet],
) -> str:
    """Build a grounded prompt for final report generation."""

    lines = [
        "Generate a concise markdown final report for a completed "
        "Phytomni remote analysis run.",
        "",
        "Ground the report only in the metadata and artifact snippets below. "
        "If evidence is missing, say so briefly.",
        "",
        f"Agent: {context.agent}",
        f"Status: {context.status}",
        f"Query: {context.query or 'Not provided'}",
        f"Tasks succeeded: {_success_count(context.live)}/{len(context.live)}",
        "",
        "Artifact snippets:",
    ]
    for snippet in snippets:
        lines.extend(
            [
                "",
                f"Artifact: {snippet.path}",
                "```text",
                snippet.content,
                "```",
            ]
        )
        if snippet.truncated:
            lines.append("Snippet was truncated by the report assembler.")
    return "\n".join(lines)


async def _summarize_with_chat(prompt: str) -> str:
    """Generate report markdown through the existing chat subgraph."""

    config = ChatConfig()
    sensitive = SensitiveConfig.load()
    chat_kwargs = build_chat_kwargs_for(
        config,
        sensitive,
        with_follow_up=False,
    )
    chat_output = await _cached_chat_app().ainvoke(
        build_chat_input(user_query=prompt, chat_kwargs=chat_kwargs)
    )
    return message_content(extract_chat_response(chat_output))
