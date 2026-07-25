# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Assemble final reports for terminal analyst-class remote runs."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

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
from .locale import SupportedLocale, locale_instruction
from .task_manager import TaskManager, resolve_tasks_db_path
from .terminal_answer import TerminalAnswerContext

__all__ = [
    "TerminalReportContext",
    "TerminalReportResult",
    "TextArtifactSnippet",
    "build_fallback_report",
    "is_terminal_report_agent",
    "persist_terminal_report",
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

_REPORT_LABELS: dict[SupportedLocale, dict[str, str]] = {
    "en-US": {
        "summary": "Summary",
        "query": "User Query",
        "task_status": "Task Status",
        "agent": "Agent",
        "status": "Status",
        "tasks": "Tasks",
        "outputs": "Outputs",
        "artifacts": "Artifacts",
        "text_artifacts_used": "Text Artifacts Used",
        "artifacts_not_summarized": "Artifacts Not Summarized",
        "degradation": "Report Degradation",
        "no_output_dirs": "No output directories were reported.",
        "no_artifacts": "No artifact files were reported.",
    },
    "zh-CN": {
        "summary": "摘要",
        "query": "用户查询",
        "task_status": "任务状态",
        "agent": "智能体",
        "status": "状态",
        "tasks": "任务",
        "outputs": "输出",
        "artifacts": "工件",
        "text_artifacts_used": "已用于总结的文本工件",
        "artifacts_not_summarized": "未总结的工件",
        "degradation": "报告降级",
        "no_output_dirs": "未报告输出目录。",
        "no_artifacts": "未报告工件文件。",
    },
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerminalReportContext(TerminalAnswerContext):
    """Inputs required to build a terminal final report.

    Inherits the reconciled-run fields (agent, status, live,
    artifacts, query) from :class:`TerminalAnswerContext`; the
    report synthesizer reads the same shape plus any future
    report-only extensions added here.
    """

    locale: SupportedLocale


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
    degraded_reason: str | None = None
    selected_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()


def is_terminal_report_agent(agent: str) -> bool:
    """Return whether ``agent`` should receive terminal report synthesis."""

    return agent in _TARGET_AGENTS


def select_text_artifact_paths(
    artifacts: Iterable[dict[str, Any]],
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
    reason: str | None = None,
    selected_paths: tuple[str, ...] = (),
    skipped_paths: tuple[str, ...] = (),
) -> TerminalReportResult:
    """Build a deterministic non-empty report without LLM output."""

    succeeded = _success_count(context.live)
    total = len(context.live)
    labels = _REPORT_LABELS[context.locale]
    title = _report_title(context.agent, context.locale)
    answer = _report_answer(context)
    lines = [
        f"# {title}",
        "",
        f"## {labels['summary']}",
        "",
        answer,
    ]
    if context.query:
        lines.extend(["", f"## {labels['query']}", "", context.query])
    lines.extend(
        [
            "",
            f"## {labels['task_status']}",
            "",
            f"- {labels['agent']}: `{context.agent}`",
            f"- {labels['status']}: `{context.status}`",
            f"- {labels['tasks']}: {succeeded}/{total} tasks succeeded",
        ]
    )
    output_dirs = [
        str(artifact.get("output_dir"))
        for artifact in context.artifacts
        if artifact.get("output_dir")
    ]
    lines.extend(["", f"## {labels['outputs']}", ""])
    if output_dirs:
        lines.extend(f"- `{output_dir}`" for output_dir in output_dirs)
    else:
        lines.append(labels["no_output_dirs"])
    artifact_paths = _all_artifact_paths(context.artifacts)
    lines.extend(["", f"## {labels['artifacts']}", ""])
    if artifact_paths:
        lines.extend(f"- `{path}`" for path in artifact_paths)
    else:
        lines.append(labels["no_artifacts"])
    if selected_paths:
        lines.extend(["", f"## {labels['text_artifacts_used']}", ""])
        lines.extend(f"- `{path}`" for path in selected_paths)
    if skipped_paths:
        lines.extend(["", f"## {labels['artifacts_not_summarized']}", ""])
        lines.extend(f"- `{path}`" for path in skipped_paths)
    degraded = bool(reason)
    if reason:
        lines.extend(
            [
                "",
                f"## {labels['degradation']}",
                "",
                _localize_report_reason(reason, context.locale),
            ]
        )
    final_report = "\n".join(lines).strip() + "\n"
    return TerminalReportResult(
        final_report=final_report,
        answer=answer,
        degraded=degraded,
        degraded_reason=reason,
        selected_paths=selected_paths,
        skipped_paths=skipped_paths,
    )


def _report_title(agent: str, locale: SupportedLocale) -> str:
    """Return a human-readable final report heading for ``agent``."""
    if locale == "zh-CN":
        titles = {
            "analyst": "分析智能体最终报告",
            "research": "计算研究最终报告",
            "design": "数字设计最终报告",
            "network": "网络分析最终报告",
        }
        return titles.get(agent, "分析最终报告")
    titles = {
        "analyst": "Analyst Final Report",
        "research": "In Silico Research Final Report",
        "design": "Digital Design Final Report",
        "network": "Network Analysis Final Report",
    }
    return titles.get(agent, "Analysis Final Report")


def _report_answer(context: TerminalReportContext) -> str:
    """Return the locale-specific deterministic terminal answer."""
    succeeded = _success_count(context.live)
    total = len(context.live)
    if context.locale == "zh-CN":
        return f"分析完成：{succeeded}/{total} 个任务成功。"
    return f"Analysis complete: {succeeded}/{total} tasks succeeded."


def _localize_report_reason(reason: str, locale: SupportedLocale) -> str:
    """Translate only known assembler-owned degradation messages."""
    if locale == "en-US":
        return reason
    known = {
        "No readable text artifacts were available for LLM summary": (
            "没有可供 LLM 总结的可读文本工件。"
        ),
        "LLM summary returned empty content": "LLM 总结返回了空内容。",
    }
    if reason in known:
        return known[reason]
    if reason.startswith("LLM summary failed: "):
        return f"LLM 总结失败：{reason.removeprefix('LLM summary failed: ')}"
    return reason


def _success_count(live: Iterable[dict[str, Any]]) -> int:
    """Count reconciled rows whose status is terminal-success."""
    return sum(
        1
        for row in live
        if str(row.get("status", "")).lower()
        in {"succeeded", "success", "completed", "done"}
    )


def _all_artifact_paths(artifacts: Iterable[dict[str, Any]]) -> list[str]:
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
    summarizer: ReportSummarizer | None = None,
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
    if summarizer is None:

        async def localized_summarizer(report_prompt: str) -> str:
            """Run the default report chat call in the persisted locale."""
            return await _summarize_with_chat(report_prompt, context.locale)

        use_summarizer: ReportSummarizer = localized_summarizer
    else:
        use_summarizer = summarizer
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
        answer=_report_answer(context),
        selected_paths=selected_paths,
        skipped_paths=combined_skipped,
    )


def _build_report_prompt(
    context: TerminalReportContext,
    snippets: Iterable[TextArtifactSnippet],
) -> str:
    """Build a grounded prompt for final report generation."""

    lines = [
        locale_instruction(context.locale),
        "",
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


async def _summarize_with_chat(
    prompt: str, locale: SupportedLocale = "en-US"
) -> str:
    """Generate report markdown through the existing chat subgraph."""

    config = ChatConfig()
    sensitive = SensitiveConfig.load()
    chat_kwargs = build_chat_kwargs_for(
        config,
        sensitive,
        with_follow_up=False,
        locale=locale,
    )
    chat_output = await _cached_chat_app().ainvoke(
        build_chat_input(user_query=prompt, chat_kwargs=chat_kwargs)
    )
    return message_content(extract_chat_response(chat_output))


def persist_terminal_report(
    live: list[dict[str, Any]],
    result: TerminalReportResult,
    *,
    task_manager: Any | None = None,
) -> None:
    """Persist ``result`` on the first task row and patch live state."""

    if not live:
        return
    row = live[0]
    task_id = row.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return
    row["final_report"] = result.final_report
    if result.degraded and result.degraded_reason:
        row["degraded"] = True
        row["degraded_reason"] = result.degraded_reason
    manager = task_manager or TaskManager(resolve_tasks_db_path())
    try:
        manager.set_task_final_report(task_id, result.final_report)
        if result.degraded and result.degraded_reason:
            manager.set_task_degraded(task_id, result.degraded_reason)
    except (sqlite3.Error, OSError) as exc:
        row["degraded_reason"] = (
            "terminal report persistence failed: " f"{type(exc).__name__}"
        )
        logger.warning(
            "failed to persist terminal report for %s: %s",
            task_id,
            exc,
        )
