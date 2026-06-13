# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthesize a thin, renderable answer for a terminal remote run.

Fire-and-forget agents (research / design / network / analyst) have no
in-process completion stage, so their answer is assembled at the
run-level reconcile-settle transition from the reconciled task rows and
the globbed artifact index. The default is pure assembly (no I/O); a
future ``AnswerSynthesizer`` can download results and run an LLM summary
without changing the call site (inputs ride a ``TerminalAnswerContext``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

__all__ = [
    "AnswerSynthesizer",
    "TerminalAnswerContext",
    "synthesize_terminal_answer",
]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_FIGURE_EXTS = (".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")


@dataclass(frozen=True)
class TerminalAnswerContext:
    """Inputs for synthesizing one terminal run's answer.

    Attributes:
        agent: Public agent slug (e.g. ``research``); available for
            per-agent phrasing in a future synthesizer.
        status: Aggregated run status (``succeeded`` / ``failed``).
        live: Reconciled child task rows.
        artifacts: ``collect_terminal_artifacts`` output (paths filled).
        query: Verbatim user query, if captured.
    """

    agent: str
    status: str
    live: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]
    query: Optional[str]


class AnswerSynthesizer(Protocol):
    """Async callable producing the terminal answer markdown."""

    async def __call__(self, context: TerminalAnswerContext) -> str: ...


async def synthesize_terminal_answer(
    context: TerminalAnswerContext,
    *,
    synthesizer: Optional[AnswerSynthesizer] = None,
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
    """Build the default structural answer (no I/O)."""
    live = context.live
    artifacts = context.artifacts
    total = len(live)
    succeeded = sum(
        1 for r in live if (r.get("status") or "").lower() in _SUCCESS_STATUSES
    )
    output_dirs = [a["output_dir"] for a in artifacts if a.get("output_dir")]
    all_paths = [p for a in artifacts for p in a.get("paths", [])]
    figures = [p for p in all_paths if p.lower().endswith(_FIGURE_EXTS)]

    lines: List[str] = []
    if context.status == "succeeded":
        lines.append(
            f"**Analysis complete** — {succeeded}/{total} tasks succeeded."
        )
    else:
        failed = total - succeeded
        lines.append(f"**Analysis failed** — {failed}/{total} tasks failed.")
    if context.query:
        lines.append("")
        lines.append(f"Query: {context.query}")
    if output_dirs:
        lines.append("")
        lines.append(
            f"Outputs — {len(output_dirs)} directories, "
            f"{len(all_paths)} files, {len(figures)} figures:"
        )
        lines.extend(f"- `{d}`" for d in output_dirs)
    if figures:
        names = ", ".join(f"`{p.rsplit('/', 1)[-1]}`" for p in figures)
        lines.append("")
        lines.append(f"Figures: {names}")
    return "\n".join(lines)
