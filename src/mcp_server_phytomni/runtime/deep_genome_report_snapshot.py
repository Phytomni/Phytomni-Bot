# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure progress and Markdown assembly for DeepGenome snapshots.

This runtime-owned module is deliberately free of agent imports so the
SQLite transition store can rebuild snapshots without a layering cycle.
The agent package re-exports the same public functions for its existing
domain-facing import path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = (
    "ReportRows",
    "assemble_intermediate_report",
    "derive_degraded_reason",
    "derive_progress",
    "derive_report_classification",
    "render_failure_notices",
)

_WORK_ITEM_STATES = (
    "planned",
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)
_NONTERMINAL_STATES = frozenset(
    {"planned", "submitted", "pending", "running", "queued", "waiting"}
)
_SUCCESS_STATES = frozenset({"succeeded", "success", "completed", "done"})
_UNAVAILABLE_REASONS = {
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
    "timed_out": "analysis task timed out",
}


@dataclass(frozen=True)
class ReportRows:
    """Immutable input bundle for one local DeepGenome snapshot."""

    sections: tuple[Mapping[str, Any], ...]
    work_items: tuple[Mapping[str, Any], ...]


def _coerce_rows(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    work_items: Sequence[Mapping[str, Any]] | None = None,
) -> ReportRows:
    """Accept a bundle or infer sections/items from one row sequence."""
    if isinstance(rows, ReportRows):
        return rows
    if work_items is not None:
        return ReportRows(tuple(rows), tuple(work_items))
    sections = tuple(row for row in rows if "work_item_key" not in row)
    inferred_items = tuple(row for row in rows if "work_item_key" in row)
    return ReportRows(sections, inferred_items)


def _status(row: Mapping[str, Any]) -> str:
    """Normalize one persisted lifecycle status."""
    value = row.get("status")
    return value.strip().lower() if isinstance(value, str) else ""


def _summary(row: Mapping[str, Any]) -> str | None:
    """Return nonblank local Markdown from one row."""
    for key in ("summary_markdown", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _key(row: Mapping[str, Any], name: str) -> str:
    """Return one safe display key without exposing arbitrary payloads."""
    value = row.get(name)
    return (
        value.strip()
        if isinstance(value, str) and value.strip()
        else "unknown"
    )


def _order(row: Mapping[str, Any]) -> tuple[int, str]:
    """Sort by stable display order, then the immutable work key."""
    value = row.get("display_order")
    order = (
        value
        if isinstance(value, int) and not isinstance(value, bool)
        else 10**9
    )
    return order, _key(row, "work_item_key")


def _ordered_sections(rows: ReportRows) -> tuple[Mapping[str, Any], ...]:
    """Return logical sections in deterministic display order."""
    return tuple(
        sorted(
            rows.sections,
            key=lambda row: (
                (
                    row.get("display_order")
                    if isinstance(row.get("display_order"), int)
                    else 10**9
                ),
                _key(row, "section_key"),
            ),
        )
    )


def _brief_gene(rows: ReportRows) -> Mapping[str, Any] | None:
    """Find the required BriefGene section."""
    return next(
        (
            row
            for row in _ordered_sections(rows)
            if row.get("section_key") == "brief_gene"
            or row.get("section_kind") == "brief_gene"
        ),
        None,
    )


def _is_usable(row: Mapping[str, Any]) -> bool:
    """Return whether one concrete item produced usable local Markdown."""
    return _status(row) in _SUCCESS_STATES and _summary(row) is not None


def _is_unusable(row: Mapping[str, Any]) -> bool:
    """Return whether a terminal item is unavailable to the report."""
    status = _status(row)
    if status in _NONTERMINAL_STATES or not status:
        return False
    return not _is_usable(row)


def _unavailable_reason(row: Mapping[str, Any]) -> str:
    """Map a terminal state to fixed local wording."""
    return _UNAVAILABLE_REASONS.get(_status(row), "analysis task unavailable")


def derive_progress(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    work_items: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, int | bool | str]:
    """Derive stable planning and concrete work-item counters."""
    normalized = _coerce_rows(rows, work_items)
    counts = {state: 0 for state in _WORK_ITEM_STATES}
    for row in normalized.work_items:
        status = _status(row)
        if status not in counts:
            status = "failed"
        counts[status] += 1
    brief = _brief_gene(normalized)
    planning_complete = bool(normalized.work_items) or any(
        row.get("section_kind") == "analysis" for row in normalized.sections
    )
    return {
        "planning_complete": planning_complete,
        "brief_gene_status": (
            _status(brief) if brief is not None else "unknown"
        ),
        "total": len(normalized.work_items),
        **counts,
    }


def _degraded(normalized: ReportRows) -> bool:
    """Return whether any observed optional item is unusable."""
    return any(_is_unusable(row) for row in normalized.work_items)


def derive_report_classification(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    work_items: Sequence[Mapping[str, Any]] | None = None,
    *,
    final_report: str | None = None,
    final_succeeded: bool = False,
) -> tuple[str, str, bool]:
    """Return report stage, completeness, and degradation deterministically."""
    normalized = _coerce_rows(rows, work_items)
    brief = _brief_gene(normalized)
    if brief is None or _status(brief) not in _SUCCESS_STATES:
        return "waiting_for_brief_gene", "none", False
    degraded = _degraded(normalized)
    if (
        final_succeeded
        and isinstance(final_report, str)
        and final_report.strip()
    ):
        return "final", "partial" if degraded else "complete", degraded
    return "intermediate", "partial", degraded


def derive_degraded_reason(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    existing_reason: str | None = None,
    work_items: Sequence[Mapping[str, Any]] | None = None,
) -> str | None:
    """Keep a sanitized reason or derive a fixed unavailable-count reason."""
    if isinstance(existing_reason, str) and existing_reason.strip():
        normalized_existing = existing_reason.strip()
        tokens = normalized_existing.split()
        generated = (
            len(tokens) == 6
            and tokens[0].isdigit()
            and tokens[1:]
            == [
                "of",
                "12",
                "optional",
                "analyses",
                "unavailable",
            ]
        )
        if not generated:
            return normalized_existing
    normalized = _coerce_rows(rows, work_items)
    unavailable = sum(_is_unusable(row) for row in normalized.work_items)
    if unavailable == 0:
        return None
    return f"{unavailable} of 12 optional analyses unavailable"


def render_failure_notices(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    work_items: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Render one fixed, sanitized Markdown notice per unavailable item."""
    normalized = _coerce_rows(rows, work_items)
    notices = [
        "### Unavailable: "
        f"{_key(row, 'work_item_key')}\n\n{_unavailable_reason(row)}."
        for row in sorted(normalized.work_items, key=_order)
        if _is_unusable(row)
    ]
    return "\n\n".join(notices)


def _title(value: str) -> str:
    """Convert a stable snake-case key to a display heading."""
    if value == "brief_gene":
        return "BriefGene"
    return value.replace("_", " ").title()


def assemble_intermediate_report(
    rows: ReportRows | Sequence[Mapping[str, Any]],
    work_items: Sequence[Mapping[str, Any]] | None = None,
) -> str | None:
    """Assemble deterministic Markdown without I/O or LLM calls."""
    normalized = _coerce_rows(rows, work_items)
    brief = _brief_gene(normalized)
    brief_summary = _summary(brief) if brief is not None else None
    if (
        brief is None
        or _status(brief) not in _SUCCESS_STATES
        or not brief_summary
    ):
        return None

    progress = derive_progress(normalized)
    parts = ["# DeepGenome Report", "## BriefGene", brief_summary]
    progress_lines = [
        "## Analysis Progress",
        f"- Planning complete: {str(progress['planning_complete']).lower()}",
        f"- BriefGene: {progress['brief_gene_status']}",
        f"- Total: {progress['total']}",
    ]
    progress_lines.extend(
        f"- {state.replace('_', ' ').title()}: {progress[state]}"
        for state in _WORK_ITEM_STATES
    )
    parts.append("\n".join(progress_lines))

    sections = {
        _key(section, "section_key"): section
        for section in _ordered_sections(normalized)
        if section is not brief
    }
    successful = [
        row
        for row in sorted(normalized.work_items, key=_order)
        if _is_usable(row)
    ]
    for section_key, section in sections.items():
        section_items = [
            row
            for row in successful
            if _key(row, "section_key") == section_key
        ]
        if not section_items:
            continue
        parts.append(f"## {_title(section_key)}")
        for row in section_items:
            if len(section_items) > 1:
                parts.append(f"### {_title(_key(row, 'work_item_key'))}")
            parts.append(_summary(row) or "")

    notices = render_failure_notices(normalized)
    if notices:
        parts.append(notices)
    return "\n\n".join(part for part in parts if part.strip())
