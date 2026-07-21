# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Immutable DeepGenome DTOs and public report projections.

The SQLite store owns transactions and lifecycle transitions.  This module
owns only value objects and the sanitized mappings that expose a snapshot to
MCP, HTTP, and task-reconciliation consumers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..contracts.deep_genome import (
    DEEP_GENOME_FINAL_FAILURE_REASONS,
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
    sanitize_nonnegative_int,
)

__all__ = [
    "DeepGenomeRemoteTaskRow",
    "DeepGenomeSectionRow",
    "DeepGenomeSnapshot",
    "snapshot_to_formatted_report_metadata",
    "snapshot_to_public_dict",
]

_PUBLIC_FAILURE_MESSAGES = {
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
    "timed_out": "analysis task timed out",
}
_PUBLIC_GENERATED_REASON = re.compile(
    r"^[0-9]+ of 12 optional analyses unavailable$"
)
_PUBLIC_REASON_FALLBACK = "analysis results are partially unavailable"


@dataclass(frozen=True)
class _SnapshotIdentity:
    """Identity fields shared by the public snapshot DTO."""

    umbrella_task_id: str
    status: str


@dataclass(frozen=True)
class _SnapshotReport:
    """Report fields shared by the public snapshot DTO."""

    intermediate_report: str | None
    final_report: str | None
    report_stage: str
    report_completeness: str
    report_revision: int
    report_updated_at: str | None


@dataclass(frozen=True)
class _SnapshotHealth:
    """Progress and degradation fields shared by the snapshot DTO."""

    progress: Mapping[str, int | bool | str]
    degraded: bool
    degraded_reason: str | None
    failures: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class DeepGenomeSnapshot(_SnapshotIdentity, _SnapshotReport, _SnapshotHealth):
    """Public-shaped local snapshot DTO used by later read paths."""


def _public_progress(
    progress: Mapping[str, int | bool | str],
) -> dict[str, int | bool | str]:
    """Project only the stable ordered progress counters."""
    planning_complete = progress.get("planning_complete")
    brief_gene_status = progress.get("brief_gene_status")
    if not isinstance(planning_complete, bool):
        planning_complete = False
    if not isinstance(brief_gene_status, str):
        brief_gene_status = "unknown"
    else:
        brief_gene_status = brief_gene_status.strip().lower() or "unknown"
    projected: dict[str, int | bool | str] = {
        "planning_complete": planning_complete,
        "brief_gene_status": brief_gene_status,
    }
    for key in DEEP_GENOME_PROGRESS_FIELDS[2:]:
        value = progress.get(key)
        projected[key] = sanitize_nonnegative_int(value)
    return projected


def _public_failures(
    failures: tuple[Mapping[str, str], ...],
) -> list[dict[str, str]]:
    """Project failures without internal reason keys or arbitrary text."""
    terminal_states = {"succeeded", "failed", "cancelled", "timed_out"}
    projected: list[dict[str, str]] = []
    for failure in failures:
        work_item_key = failure.get("work_item_key")
        status = failure.get("status")
        if (
            not isinstance(work_item_key, str)
            or not work_item_key.strip()
            or status not in terminal_states
        ):
            continue
        projected.append(
            {
                "work_item_key": work_item_key.strip(),
                "status": status,
                "message": _PUBLIC_FAILURE_MESSAGES.get(
                    status, "analysis task unavailable"
                ),
            }
        )
    return projected


def _public_degraded_reason(value: str | None) -> str | None:
    """Keep fixed reasons and replace unexpected legacy text."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if _PUBLIC_GENERATED_REASON.fullmatch(normalized):
        return normalized
    if normalized in DEEP_GENOME_FINAL_FAILURE_REASONS:
        return normalized
    return _PUBLIC_REASON_FALLBACK


def _public_timestamp(value: str | None) -> str | None:
    """Return one valid timestamp in canonical UTC form, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def snapshot_to_public_dict(
    snapshot: DeepGenomeSnapshot,
) -> dict[str, Any]:
    """Serialize one DeepGenome snapshot for MCP and HTTP consumers."""
    stage = snapshot.report_stage
    if stage not in {"waiting_for_brief_gene", "intermediate", "final"}:
        stage = "waiting_for_brief_gene"
    completeness = snapshot.report_completeness
    if completeness not in {"none", "partial", "complete"}:
        completeness = "none"
    revision = snapshot.report_revision
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
    ):
        revision = 0
    public = {
        "intermediate_report": (
            snapshot.intermediate_report
            if isinstance(snapshot.intermediate_report, str)
            else None
        ),
        "final_report": (
            snapshot.final_report
            if isinstance(snapshot.final_report, str)
            else None
        ),
        "report_stage": stage,
        "report_completeness": completeness,
        "report_revision": revision,
        "report_updated_at": _public_timestamp(snapshot.report_updated_at),
        "progress": _public_progress(snapshot.progress),
        "degraded": bool(snapshot.degraded),
        "degraded_reason": _public_degraded_reason(snapshot.degraded_reason),
        "failures": _public_failures(snapshot.failures),
    }
    return {field: public[field] for field in DEEP_GENOME_REPORT_FIELDS}


def snapshot_to_formatted_report_metadata(
    snapshot: DeepGenomeSnapshot,
) -> dict[str, Any]:
    """Return additive structured report metadata for one snapshot.

    Every value is derived from :func:`snapshot_to_public_dict` so the
    formatted envelope cannot expose a less-sanitized view than the
    established top-level projection.
    """
    public = snapshot_to_public_dict(snapshot)
    return {
        "stage": public["report_stage"],
        "completeness": public["report_completeness"],
        "revision": public["report_revision"],
        "updated_at": public["report_updated_at"],
        "progress": public["progress"],
        "degraded": public["degraded"],
        "failure_count": len(public["failures"]),
    }


@dataclass(frozen=True)
class _SectionIdentity:
    """Identity fields for one logical section row."""

    umbrella_task_id: str
    section_key: str
    section_kind: str
    display_order: int


@dataclass(frozen=True)
class _SectionContent:
    """State and timestamps for one logical section row."""

    status: str
    summary_markdown: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeepGenomeSectionRow(_SectionIdentity, _SectionContent):
    """Immutable row projection for one logical DeepGenome section."""


@dataclass(frozen=True)
class _RemoteIdentity:
    """Identity fields for one concrete remote work item."""

    umbrella_task_id: str
    section_key: str
    work_item_key: str
    status: str


@dataclass(frozen=True)
class _RemoteContent:
    """Remote identities, content, and timestamps for one work item."""

    submitted_task_id: str | None
    poll_task_id: str | None
    summary_markdown: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeepGenomeRemoteTaskRow(_RemoteIdentity, _RemoteContent):
    """Immutable row projection for one concrete remote work item."""
