# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Assemble final reports for terminal analyst-class remote runs."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Mapping
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
from ..mcp.formatting.models import ReportExecution
from ..storage.downloads import download_obs_file
from .artifact_roles import ArtifactRole, ClassifiedArtifact
from .execution_models import ExecutionWarning
from .locale import SupportedLocale, locale_instruction
from .task_manager import TaskManager, resolve_tasks_db_path
from .terminal_answer import TerminalAnswerContext

__all__ = [
    "TerminalReportContext",
    "TerminalReportAssembly",
    "TerminalReportResult",
    "TextArtifactSnippet",
    "assemble_terminal_report",
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
_DIRECT_CONCLUSION_MAX_CHARS = 4_096
_PLAIN_CONCLUSION_ROLES = frozenset(
    {ArtifactRole.UNKNOWN, ArtifactRole.SCIENTIFIC_DATA}
)
_PLAIN_CONCLUSION_MEDIA_PREFIXES = (
    "text/plain",
    "text/markdown",
    "text/csv",
)
_MAX_TOTAL_PROMPT_CHARS = 120_000
_SUMMARY_TIMEOUT_SECONDS = 90.0
_REPORT_TEMP_DIR = "terminal-report"
_REPORT_WARNING_STAGE = "terminal_report"
_REPORT_TEXT_ROLES = frozenset(
    {
        ArtifactRole.SCIENTIFIC_REPORT,
        ArtifactRole.SCIENTIFIC_TABLE,
        ArtifactRole.SCIENTIFIC_TEXT,
    }
)

_NO_TEXT_FALLBACK: dict[SupportedLocale, str] = {
    "en-US": (
        "The analysis reached a terminal outcome, but no validated "
        "scientific text artifact was available for synthesis. Review the "
        "downloadable scientific artifacts and execution warnings before "
        "drawing conclusions."
    ),
    "zh-CN": (
        "分析已到达终态，但没有可用于综合的已验证科学文本产物。"
        "在形成结论前，请结合可下载的科学产物和执行警告进行审阅。"
    ),
}
_SYNTHESIS_FAILURE_FALLBACK: dict[SupportedLocale, str] = {
    "en-US": (
        "The analysis reached a terminal outcome, but scientific report "
        "synthesis was unavailable. The validated scientific artifacts "
        "remain available for review before drawing conclusions."
    ),
    "zh-CN": (
        "分析已到达终态，但科学报告综合不可用。"
        "在形成结论前，仍可审阅已验证的科学产物。"
    ),
}

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
        "analysis_result": "Analysis result",
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
        "analysis_result": "分析结果",
    },
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerminalReportContext(TerminalAnswerContext):
    """Inputs required to build a terminal final report.

    Inherits the reconciled-run fields (agent, status, live,
    artifacts, query, locale) from :class:`TerminalAnswerContext`.
    The canonical assembler receives classified artifacts separately so
    legacy path-only descriptors cannot silently enter report context.
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
    degraded_reason: str | None = None
    selected_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TerminalReportAssembly:
    """Canonical report answer plus safe execution metadata."""

    answer: str
    report: ReportExecution
    warnings: tuple[ExecutionWarning, ...] = ()


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
    """Build a deterministic non-empty report without LLM output.

    ``selected_paths`` and ``skipped_paths`` remain compatibility metadata
    for internal callers, but neither they nor the artifact index is copied
    into user-visible prose.
    """

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
ReportArtifact = ClassifiedArtifact | Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _ReportArtifactSnippet:
    """Safe report section with a logical name instead of a source path."""

    name: str
    role: str
    content: str
    truncated: bool


def _report_warning(code: str, *, retryable: bool = False) -> ExecutionWarning:
    """Build one stable report warning without exception details."""
    return ExecutionWarning(
        code=code,
        retryable=retryable,
        stage=_REPORT_WARNING_STAGE,
    )


def _artifact_role(artifact: ReportArtifact) -> ArtifactRole | None:
    """Return an explicit producer role, never an extension-derived role."""
    if isinstance(artifact, ClassifiedArtifact):
        return artifact.role
    raw_role = artifact.get("role")
    if not isinstance(raw_role, str):
        return None
    try:
        return ArtifactRole(raw_role)
    except (TypeError, ValueError):
        return None


def _artifact_media_type(artifact: ReportArtifact) -> str:
    """Return the classified media type, or empty when it is missing."""
    if isinstance(artifact, ClassifiedArtifact):
        return artifact.media_type
    value = artifact.get("media_type")
    return value if isinstance(value, str) else ""


def _artifact_is_plain_conclusion(artifact: ReportArtifact) -> bool:
    """Return whether an undeclared text file may be the user-facing answer."""
    if _artifact_role(artifact) not in _PLAIN_CONCLUSION_ROLES:
        return False
    media = _artifact_media_type(artifact).strip().lower()
    return any(
        media.startswith(prefix) for prefix in _PLAIN_CONCLUSION_MEDIA_PREFIXES
    )


def _format_plain_conclusion(
    context: TerminalReportContext,
    snippets: tuple[_ReportArtifactSnippet, ...],
) -> str:
    """Build a deterministic official body from small conclusion files."""
    labels = _REPORT_LABELS.get(context.locale, _REPORT_LABELS["en-US"])
    parts: list[str] = [labels["analysis_result"]]
    query = context.query.strip() if isinstance(context.query, str) else ""
    if query:
        parts.append(f"{labels['query']}: {query}")
    for snippet in snippets:
        body = snippet.content.strip()
        if snippet.name and body:
            parts.append(f"{snippet.name}\n{body}")
        elif body:
            parts.append(body)
        elif snippet.name:
            parts.append(snippet.name)
    return "\n\n".join(parts).strip()


def _artifact_is_report_eligible(artifact: ReportArtifact) -> bool:
    """Return whether one classified artifact may enter report context."""
    role = _artifact_role(artifact)
    if role not in _REPORT_TEXT_ROLES:
        return False
    if isinstance(artifact, Mapping):
        declared = artifact.get("report_context_eligible")
        if declared is not None and declared is not True:
            return False
    return True


def _artifact_size_bytes(artifact: ReportArtifact) -> int | None:
    """Read the verified object size when the classified record has one."""
    if isinstance(artifact, ClassifiedArtifact):
        return artifact.size_bytes
    value = artifact.get("size_bytes")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _artifact_name(artifact: ReportArtifact) -> str:
    """Return a basename-only logical label for prompt sections."""
    if isinstance(artifact, ClassifiedArtifact):
        return artifact.to_public().name
    for key in ("name", "relative_path", "path"):
        value = artifact.get(key)
        if isinstance(value, str) and value:
            return PurePosixPath(value.replace("\\", "/")).name
    return "scientific-artifact"


def _artifact_read_reference(artifact: ReportArtifact) -> str:
    """Return an internal source reference used only by the reader."""
    if isinstance(artifact, ClassifiedArtifact):
        reference = artifact.source_path or artifact.download_ref
    else:
        reference = next(
            (
                artifact.get(key)
                for key in ("source_path", "download_ref", "path")
                if isinstance(artifact.get(key), str) and artifact.get(key)
            ),
            None,
        )
    if not isinstance(reference, str) or not reference:
        raise OSError("artifact source reference unavailable")
    return reference


async def _read_report_artifact(reference: str) -> str:
    """Read one local or OBS-backed artifact without exposing its reference."""
    source_path = Path(reference)
    if await asyncio.to_thread(source_path.is_file):
        return await asyncio.to_thread(
            source_path.read_text,
            encoding="utf-8",
        )
    return await read_obs_text_artifact(reference)


def _bounded_utf8_text(
    content: str,
    *,
    max_bytes: int,
    max_chars: int,
) -> tuple[str, bool]:
    """Apply byte and total-character caps without splitting UTF-8 text."""
    encoded = content.encode("utf-8")
    truncated = len(encoded) > max_bytes
    if truncated:
        content = encoded[:max_bytes].decode("utf-8", errors="ignore")
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return content, truncated


async def _admit_report_artifacts(
    artifacts: Iterable[ReportArtifact],
    *,
    reader: ArtifactTextReader,
    is_eligible: Callable[[ReportArtifact], bool] | None = None,
) -> tuple[tuple[_ReportArtifactSnippet, ...], tuple[ExecutionWarning, ...]]:
    """Admit only manifest roles into bounded report context."""
    eligibility = is_eligible or _artifact_is_report_eligible
    eligible = tuple(
        artifact for artifact in artifacts if eligibility(artifact)
    )
    warnings: list[ExecutionWarning] = []
    if len(eligible) > _MAX_TEXT_ARTIFACTS:
        warnings.append(_report_warning("report_artifact_count_capped"))
        eligible = eligible[:_MAX_TEXT_ARTIFACTS]

    snippets: list[_ReportArtifactSnippet] = []
    remaining = _MAX_TOTAL_PROMPT_CHARS
    truncated_warning_added = False
    for artifact in eligible:
        if remaining <= 0:
            if not truncated_warning_added:
                warnings.append(_report_warning("report_context_truncated"))
                truncated_warning_added = True
            break
        size_bytes = _artifact_size_bytes(artifact)
        if size_bytes is not None and size_bytes > _MAX_BYTES_PER_ARTIFACT:
            warnings.append(_report_warning("report_artifact_size_exceeded"))
            continue
        try:
            content = await reader(_artifact_read_reference(artifact))
            if not isinstance(content, str):
                raise TypeError("artifact reader must return text")
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            UnicodeError,
            ValueError,
            TimeoutError,
        ):
            warnings.append(_report_warning("report_artifact_read_failed"))
            continue
        bounded, truncated = _bounded_utf8_text(
            content,
            max_bytes=_MAX_BYTES_PER_ARTIFACT,
            max_chars=remaining,
        )
        if not bounded.strip():
            warnings.append(_report_warning("report_artifact_empty"))
            continue
        role = _artifact_role(artifact)
        if role is None:
            continue
        snippets.append(
            _ReportArtifactSnippet(
                name=_artifact_name(artifact),
                role=role.value,
                content=bounded,
                truncated=truncated,
            )
        )
        remaining -= len(bounded)
        if truncated and not truncated_warning_added:
            warnings.append(_report_warning("report_context_truncated"))
            truncated_warning_added = True
    return tuple(snippets), tuple(warnings)


def _report_scope(context: TerminalReportContext) -> str:
    """Describe completion scope without task or infrastructure details."""
    succeeded = _success_count(context.live)
    total = len(context.live)
    if context.locale == "zh-CN":
        return f"本次终态包含 {total} 个任务，其中 {succeeded} 个任务成功。"
    return (
        f"The terminal outcome covered {total} tasks, "
        f"with {succeeded} successful."
    )


def _no_text_assembly(
    context: TerminalReportContext,
    warnings: Iterable[ExecutionWarning],
) -> TerminalReportAssembly:
    """Return the fixed degraded result for a role-empty context."""
    return TerminalReportAssembly(
        answer=_NO_TEXT_FALLBACK[context.locale],
        report=ReportExecution(
            state="degraded",
            degraded=True,
            source_artifact_count=0,
        ),
        warnings=(
            _report_warning("report_no_scientific_text"),
            *tuple(warnings),
        ),
    )


def _failed_synthesis_assembly(
    context: TerminalReportContext,
    warnings: Iterable[ExecutionWarning],
    *,
    source_artifact_count: int,
) -> TerminalReportAssembly:
    """Return a fixed degraded result after synthesis failure."""
    base = _SYNTHESIS_FAILURE_FALLBACK[context.locale]
    answer = f"{base} {_report_scope(context)}"
    return TerminalReportAssembly(
        answer=answer,
        report=ReportExecution(
            state="degraded",
            degraded=True,
            source_artifact_count=source_artifact_count,
        ),
        warnings=(
            *tuple(warnings),
            _report_warning("report_synthesis_failed"),
        ),
    )


def _generated_report_contains_operational_data(
    context: TerminalReportContext,
    artifacts: Iterable[ReportArtifact],
    report: str,
) -> bool:
    """Reject model output that echoes private run or infrastructure data."""
    lowered = report.casefold()
    if re.search(r"(?<!\w)/(?:obs|tmp|home|mnt|var|private)/", lowered):
        return True
    for marker in (
        "s3://",
        "oss://",
        "file://",
        "execution log",
        "provider:",
        "hardware:",
        "task id:",
        "run id:",
        "job id:",
    ):
        if marker in lowered:
            return True
    values: list[str] = []
    for row in context.live:
        for key in ("task_id", "run_id", "provider", "hardware", "job_id"):
            value = row.get(key)
            if isinstance(value, str) and len(value) >= 4:
                values.append(value)
    for artifact in artifacts:
        if isinstance(artifact, ClassifiedArtifact):
            values.extend(
                value
                for value in (artifact.source_path, artifact.download_ref)
                if isinstance(value, str) and len(value) >= 4
            )
        else:
            for key in ("source_path", "download_ref", "output_dir"):
                value = artifact.get(key)
                if isinstance(value, str) and len(value) >= 4:
                    values.append(value)
    return any(value.casefold() in lowered for value in values)


async def assemble_terminal_report(
    *,
    context: TerminalReportContext,
    artifacts: Iterable[ReportArtifact],
    reader: ArtifactTextReader | None = None,
    summarizer: ReportSummarizer | None = None,
) -> TerminalReportAssembly:
    """Assemble a role-gated report with a safe deterministic floor."""
    artifact_values = tuple(artifacts)
    use_reader = reader or _read_report_artifact
    snippets, warnings = await _admit_report_artifacts(
        artifact_values,
        reader=use_reader,
    )
    if not snippets:
        snippets, extra_warnings = await _admit_report_artifacts(
            artifact_values,
            reader=use_reader,
            is_eligible=_artifact_is_plain_conclusion,
        )
        warnings = warnings + extra_warnings
        if not snippets:
            return _no_text_assembly(context, warnings)
        total_chars = sum(len(snippet.content) for snippet in snippets)
        if total_chars <= _DIRECT_CONCLUSION_MAX_CHARS:
            return TerminalReportAssembly(
                answer=_format_plain_conclusion(context, snippets),
                report=ReportExecution(
                    state="final",
                    degraded=False,
                    source_artifact_count=len(snippets),
                ),
                warnings=warnings,
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
        generated = await asyncio.wait_for(
            use_summarizer(prompt),
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )
    except (
        OSError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValueError,
        TimeoutError,
    ):
        return _failed_synthesis_assembly(
            context,
            warnings,
            source_artifact_count=len(snippets),
        )
    if not isinstance(generated, str):
        return _failed_synthesis_assembly(
            context,
            warnings,
            source_artifact_count=len(snippets),
        )
    generated = generated.strip()
    if not generated or _generated_report_contains_operational_data(
        context,
        artifact_values,
        generated,
    ):
        return _failed_synthesis_assembly(
            context,
            warnings,
            source_artifact_count=len(snippets),
        )
    return TerminalReportAssembly(
        answer=generated,
        report=ReportExecution(
            state="final",
            degraded=False,
            source_artifact_count=len(snippets),
        ),
        warnings=warnings,
    )


async def synthesize_terminal_report(
    context: TerminalReportContext,
    *,
    reader: ArtifactTextReader = _read_report_artifact,
    summarizer: ReportSummarizer | None = None,
) -> TerminalReportResult:
    """Return the legacy shape projected from role-gated assembly."""

    assembly = await assemble_terminal_report(
        context=context,
        artifacts=context.artifacts,
        reader=reader,
        summarizer=summarizer,
    )
    degraded_reason = next(
        (
            warning.code
            for warning in assembly.warnings
            if warning.code.startswith("report_")
        ),
        None,
    )
    return TerminalReportResult(
        final_report=assembly.answer,
        answer=_report_answer(context),
        degraded=assembly.report.degraded,
        degraded_reason=degraded_reason,
    )


def _build_report_prompt(
    context: TerminalReportContext,
    snippets: Iterable[TextArtifactSnippet | _ReportArtifactSnippet],
) -> str:
    """Build a grounded prompt for final report generation."""

    lines = [
        locale_instruction(context.locale),
        "",
        "Generate a concise markdown final report for a terminal "
        "Phytomni analysis run.",
        "",
        "Ground the report only in the user query, task outcome, and "
        "scientific artifact sections below. If evidence is missing, say so "
        "briefly.",
        "Include these sections: Results, Methods, Limitations, and "
        "Scientific context.",
        "Do not mention source or output paths, raw logs, providers, "
        "hardware, task IDs, run IDs, or orchestration details.",
        "",
        f"Agent: {context.agent}",
        f"Status: {context.status}",
        f"Query: {context.query or 'Not provided'}",
        f"Tasks succeeded: {_success_count(context.live)}/{len(context.live)}",
        "",
        "Artifact snippets:",
    ]
    for snippet in snippets:
        if isinstance(snippet, _ReportArtifactSnippet):
            lines.extend(
                [
                    "",
                    f"Scientific artifact: {snippet.name}",
                    f"Role: {snippet.role}",
                    "```text",
                    snippet.content,
                    "```",
                ]
            )
        else:
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
