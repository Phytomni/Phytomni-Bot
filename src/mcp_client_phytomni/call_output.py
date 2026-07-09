# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Human-readable stdout rendering for phytomni call results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .tool_result_formatters import FormattedToolResult

__all__ = ["render_call_output"]


def render_call_output(formatted: FormattedToolResult) -> str:
    """Return human-readable stdout for one phytomni call result."""
    parts: list[str] = [formatted.answer]

    tabular = formatted.tabular
    if isinstance(tabular, Mapping):
        headers = tabular.get("headers")
        rows = tabular.get("rows")
        if headers is not None and rows is not None:
            parts.append("---")
            parts.append("\t".join(str(cell) for cell in headers))
            for row in rows:
                parts.append("\t".join(str(cell) for cell in row))

    if formatted.references:
        parts.append("---")
        for index, ref in enumerate(formatted.references, start=1):
            title = (
                ref.get("ti") or ref.get("title") or ref.get("file_id") or ""
            )
            parts.append(f"[{index}] {title}")

    metadata_line = _format_task_metadata_line(formatted.metadata)
    if metadata_line is not None:
        parts.append("---")
        parts.append(metadata_line)

    return "\n".join(parts).rstrip()


def _format_task_metadata_line(
    metadata: Mapping[str, Any],
) -> str | None:
    """Build the optional task metadata line when relevant keys are present."""
    failures = metadata.get("failures")
    has_failures = (
        isinstance(failures, Sequence)
        and not isinstance(failures, (str, bytes))
        and len(failures) > 0
    )
    has_task_fields = any(
        key in metadata for key in ("task_id", "output_dir", "status")
    )
    if not has_task_fields and not has_failures:
        return None

    tokens: list[str] = []
    if "task_id" in metadata:
        tokens.append(f"task_id={metadata['task_id']}")
    if "output_dir" in metadata:
        tokens.append(f"output_dir={metadata['output_dir']}")
    if "status" in metadata:
        tokens.append(f"status={metadata['status']}")
    if (
        isinstance(failures, Sequence)
        and not isinstance(failures, (str, bytes))
        and failures
    ):
        tokens.append(f"failures={len(failures)}")
    return " ".join(tokens)
