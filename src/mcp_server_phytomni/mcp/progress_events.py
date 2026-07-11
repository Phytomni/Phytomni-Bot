# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Structured progress-event vocabulary for graph-agent long calls.

Nodes emit through :func:`emit_progress`; the SSE and MCP stdio seams
project the event into their respective frames. Outside a runnable
context the helper is a silent no-op. Protocol adapters may preserve
these fields as progress metadata, but must derive their task lifecycle
state independently.
"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

from langgraph.config import get_stream_writer

PROGRESS_KIND: Final = "phyto.progress"


class ProgressEvent(TypedDict):
    """One structured progress tick emitted from inside a graph node.

    Attributes:
        kind: Discriminator; always ``"phyto.progress"`` so the custom
            seam can tell progress ticks from other custom payloads.
        phase: Semantic stage label, e.g. ``"retrieving"``/``"drafting"``.
        current: Monotonic completed-count within the stage.
        total: Denominator when known, else ``None``.
        detail: Optional human-readable note, e.g. ``"gene 3/8"``.
    """

    kind: Literal["phyto.progress"]
    phase: str
    current: int
    total: int | None
    detail: str | None


def emit_progress(
    phase: str,
    current: int,
    total: int | None = None,
    detail: str | None = None,
) -> None:
    """Emit one progress tick through the active LangGraph stream writer.

    Safe from any execution path: ``get_stream_writer()`` raises
    ``RuntimeError`` outside a runnable context (blocking ``ainvoke``,
    direct unit calls), which this function swallows so the calling
    node never breaks. When a writer is active the full
    :class:`ProgressEvent` is written for the custom seam to project.

    Args:
        phase: Semantic stage label.
        current: Monotonic completed-count within the stage.
        total: Denominator when known, else ``None``.
        detail: Optional human-readable note.
    """
    event: ProgressEvent = {
        "kind": "phyto.progress",
        "phase": phase,
        "current": current,
        "total": total,
        "detail": detail,
    }
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(event)
