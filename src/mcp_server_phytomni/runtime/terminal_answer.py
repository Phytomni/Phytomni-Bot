# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthesize a thin, renderable answer for a terminal remote run.

The answer surface is deliberately outcome-only. Artifact paths, filenames,
logs, and provider details belong to the execution projection and must not be
assembled into user-visible terminal prose. Rich scientific synthesis lives
in :mod:`terminal_report` and uses an explicit producer role manifest.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .locale import SupportedLocale

__all__ = [
    "AnswerSynthesizer",
    "TerminalAnswerContext",
    "synthesize_terminal_answer",
]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})


@dataclass(frozen=True)
class TerminalAnswerContext:
    """Inputs for synthesizing one terminal run's answer.

    Attributes:
        agent: Public agent slug (e.g. ``research``); available for
            per-agent phrasing in a future synthesizer.
        status: Aggregated run status (``succeeded`` / ``failed``).
        live: Reconciled child task rows.
        artifacts: Terminal artifact descriptors. The default assembler uses
            only their count and never reads their content.
        query: Verbatim user query, if captured.
        locale: Effective locale persisted with the run.
    """

    agent: str
    status: str
    live: list[dict[str, Any]]
    artifacts: Sequence[Any]
    query: str | None
    locale: SupportedLocale = "en-US"


AnswerSynthesizer = Callable[[TerminalAnswerContext], Awaitable[str]]
"""Async callable producing the terminal answer markdown."""


async def synthesize_terminal_answer(
    context: TerminalAnswerContext,
    *,
    synthesizer: AnswerSynthesizer | None = None,
) -> str:
    """Return a renderable markdown answer for a terminal remote run.

    Args:
        context: Bundled inputs for the answer.
        synthesizer: Override the thin default (e.g. an LLM report).

    Returns:
        Markdown answer string.
    """
    if synthesizer is not None:
        return await synthesizer(context)
    return _thin_answer(context)


def _thin_answer(context: TerminalAnswerContext) -> str:
    """Build the default structural answer without artifact ingestion."""
    live = context.live
    total = len(live)
    succeeded = sum(
        1 for r in live if (r.get("status") or "").lower() in _SUCCESS_STATUSES
    )
    if context.locale == "zh-CN":
        if context.status == "succeeded":
            lines = [f"分析完成：{succeeded}/{total} 个任务成功。"]
        else:
            failed = total - succeeded
            lines = [f"分析失败：{failed}/{total} 个任务失败。"]
        query_label = "查询"
    elif context.status == "succeeded":
        lines = [
            f"**Analysis complete** — {succeeded}/{total} tasks succeeded."
        ]
        query_label = "Query"
    else:
        failed = total - succeeded
        lines = [f"**Analysis failed** — {failed}/{total} tasks failed."]
        query_label = "Query"
    if context.query:
        lines.append("")
        lines.append(f"{query_label}: {context.query}")
    return "\n".join(lines)
