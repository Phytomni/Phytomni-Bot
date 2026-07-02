# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Assemble final reports for terminal analyst-class remote runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Optional

from .terminal_answer import TerminalAnswerContext

__all__ = [
    "TerminalReportContext",
    "TerminalReportResult",
    "TextArtifactSnippet",
    "is_terminal_report_agent",
    "select_text_artifact_paths",
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
