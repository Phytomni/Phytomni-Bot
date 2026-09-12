# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Immutable DeepGenome DTOs and public report projections.

The SQLite store owns transactions and lifecycle transitions.  This module
owns only value objects and the sanitized mappings that expose a snapshot to
MCP, HTTP, and task-reconciliation consumers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from ..agents.shared.citation_enrichment import CITATION_BIBLIO_FIELDS
from ..contracts.deep_genome import (
    DEEP_GENOME_FINAL_FAILURE_REASONS,
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
    sanitize_nonnegative_int,
)
from ..mcp.formatting.cited import normalize_citations
from ..mcp.formatting.execution import (
    PUBLIC_ARTIFACT_KEYS,
    apply_compatibility_projection,
    build_execution_projection,
)
from ..mcp.formatting.models import (
    ExecutionProjection,
    FormattedToolResult,
    ReportExecution,
)
from .execution_models import ExecutionWarning
from .run_registry_delivery import result_delivery_from_result

__all__ = [
    "DeepGenomeRemoteTaskRow",
    "DeepGenomeSectionRow",
    "DeepGenomeSnapshot",
    "public_snapshot_to_canonical_result",
    "sanitize_deep_genome_snapshot",
    "snapshot_to_canonical_result",
    "snapshot_to_formatted_report_metadata",
    "snapshot_metadata_from_mapping",
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
_ReportState = Literal["none", "intermediate", "final", "degraded"]
_PUBLIC_STATUSES = frozenset(
    {"running", "succeeded", "failed", "cancelled", "timed_out"}
)
_SAFE_METADATA_KEYS = frozenset(
    {
        "consumer",
        "gene",
        "gene_id",
        "original_query",
        "query",
        "resolve_gene_id",
        "resolved_gene_id",
        "resolved_species_code",
        "species",
        "species_code",
    }
)
_SAFE_REFERENCE_KEYS = (
    "file_id",
    "title",
    *CITATION_BIBLIO_FIELDS,
)


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
            or not isinstance(status, str)
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


def _optional_string(value: Any) -> str | None:
    """Keep public text or null; reject non-string persisted values."""
    return value if isinstance(value, str) else None


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


def sanitize_deep_genome_snapshot(
    source: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Allowlist one legacy persisted snapshot mapping."""
    stage = source.get("report_stage")
    completeness = source.get("report_completeness")
    revision = source.get("report_revision")
    if not isinstance(stage, str) or stage not in {
        "waiting_for_brief_gene",
        "intermediate",
        "final",
    }:
        return None
    if not isinstance(completeness, str) or completeness not in {
        "none",
        "partial",
        "complete",
    }:
        return None
    if not isinstance(revision, int) or isinstance(revision, bool):
        return None
    raw_failures = source.get("failures")
    failures = (
        tuple(value for value in raw_failures if isinstance(value, Mapping))
        if isinstance(raw_failures, list)
        else ()
    )
    public = {
        "intermediate_report": _optional_string(
            source.get("intermediate_report")
        ),
        "final_report": _optional_string(source.get("final_report")),
        "report_stage": stage,
        "report_completeness": completeness,
        "report_revision": sanitize_nonnegative_int(revision),
        "report_updated_at": _public_timestamp(
            source.get("report_updated_at")
        ),
        "progress": _public_progress(_mapping(source.get("progress"))),
        "degraded": source.get("degraded") is True,
        "degraded_reason": _public_degraded_reason(
            source.get("degraded_reason")
        ),
        "failures": _public_failures(failures),
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


def snapshot_metadata_from_mapping(
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Project bounded progress metadata without report-state internals."""
    stage = source.get("stage")
    completeness = source.get("completeness")
    revision = source.get("revision")
    failure_count = source.get("failure_count")
    raw_failures = source.get("failures")
    if failure_count is None and isinstance(raw_failures, list):
        failure_count = len(
            _public_failures(
                tuple(
                    value
                    for value in raw_failures
                    if isinstance(value, Mapping)
                )
            )
        )
    return {
        "stage": (
            stage
            if stage in {"waiting_for_brief_gene", "intermediate", "final"}
            else "waiting_for_brief_gene"
        ),
        "completeness": (
            completeness
            if completeness in {"none", "partial", "complete"}
            else "none"
        ),
        "revision": sanitize_nonnegative_int(revision),
        "updated_at": _public_timestamp(source.get("updated_at")),
        "progress": _public_progress(_mapping(source.get("progress"))),
        "degraded": source.get("degraded") is True,
        "failure_count": sanitize_nonnegative_int(failure_count),
    }


def snapshot_to_canonical_result(
    snapshot: DeepGenomeSnapshot,
    *,
    existing_result: Mapping[str, Any] | None = None,
    preserve_citation_indices: bool = False,
) -> dict[str, Any]:
    """Build canonical report data with source indices when persisting."""
    return public_snapshot_to_canonical_result(
        snapshot_to_public_dict(snapshot),
        task_id=snapshot.umbrella_task_id,
        status=snapshot.status,
        existing_result=existing_result,
        preserve_citation_indices=preserve_citation_indices,
    )


def public_snapshot_to_canonical_result(
    snapshot: Mapping[str, Any],
    *,
    task_id: str,
    status: str,
    existing_result: Mapping[str, Any] | None = None,
    preserve_citation_indices: bool = False,
) -> dict[str, Any]:
    """Build a canonical result from an already-sanitized snapshot mapping."""
    public_status = _public_status(status)
    existing = build_execution_projection(
        "DeepGenomeAgent", existing_result or {}
    )
    output_dirs = _public_output_dirs(existing_result)
    execution = _execution_for_public_snapshot(
        snapshot,
        task_id=task_id,
        public_status=public_status,
        existing=existing,
        existing_result=existing_result,
    )
    metadata = _public_metadata(existing_result)
    metadata["deep_genome"] = _snapshot_metadata(snapshot)
    answer, references = _bind_snapshot_citations(
        snapshot,
        existing_result,
        preserve_citation_indices=preserve_citation_indices,
    )
    formatted = apply_compatibility_projection(
        FormattedToolResult(
            answer=answer,
            follow_up_questions=_existing_follow_up_questions(existing_result),
            metadata=metadata,
            references=references,
            tabular=_existing_tabular(existing_result),
            output_dirs=tuple(output_dirs),
        ),
        execution,
    )
    return _json_compatible(
        {"formatted": asdict(formatted), "execution": asdict(execution)}
    )


def _bind_snapshot_citations(
    snapshot: Mapping[str, Any],
    existing_result: Mapping[str, Any] | None,
    *,
    preserve_citation_indices: bool = False,
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    """Bind report superscripts to stored references when both exist."""
    report = _best_report(snapshot)
    corpus = _existing_references(existing_result)
    # The stored source report must keep its full positional corpus so future
    # HTTP reads do not bind it against an already-pruned citation list.
    if report and corpus and not preserve_citation_indices:
        bound_answer, bound_refs = normalize_citations(report, corpus)
        if bound_refs:
            return bound_answer, bound_refs
    return report, corpus


def _execution_for_public_snapshot(
    snapshot: Mapping[str, Any],
    *,
    task_id: str,
    public_status: str,
    existing: ExecutionProjection,
    existing_result: Mapping[str, Any] | None,
) -> ExecutionProjection:
    """Build execution warnings and the public task descriptor."""
    artifacts = _public_artifacts(existing_result)
    output_dirs = _public_output_dirs(existing_result)
    warnings = list(existing.warnings)
    diagnostics = list(existing.diagnostics)
    degraded = bool(snapshot.get("degraded"))
    if degraded:
        _append_warning(
            warnings,
            ExecutionWarning(
                code="deep_genome_report_degraded",
                stage="deep_genome",
            ),
        )
        _append_diagnostic(
            diagnostics,
            {"code": "deep_genome_report_degraded", "stage": "deep_genome"},
        )
    if public_status != "succeeded":
        failure_code = (
            "task_failed"
            if _snapshot_has_failures(snapshot)
            else "run_not_succeeded"
        )
        _append_warning(
            warnings,
            ExecutionWarning(code=failure_code, stage="reconcile"),
        )
        _append_diagnostic(
            diagnostics,
            {"code": failure_code, "stage": "reconcile"},
        )
    return ExecutionProjection(
        tracking={
            "degraded": degraded
            or public_status != "succeeded"
            or bool(existing.tracking.get("degraded")),
        },
        warnings=tuple(warnings),
        tasks=(
            {
                "id": task_id,
                "accepted": True,
                "status": public_status,
            },
        ),
        artifacts=tuple(artifacts),
        output_dirs=tuple(output_dirs),
        report=ReportExecution(
            state=_report_state(snapshot),
            degraded=degraded,
            source_artifact_count=len(artifacts),
        ),
        diagnostics=tuple(diagnostics),
        delivery=result_delivery_from_result(existing_result),
    )


def _report_state(snapshot: Mapping[str, Any]) -> _ReportState:
    """Map the bounded snapshot stage to the report state contract."""
    stage = snapshot.get("report_stage")
    if stage in {"intermediate", "final"}:
        return cast(_ReportState, stage)
    return "none"


def _best_report(snapshot: Mapping[str, Any]) -> str:
    """Return the newest nonblank scientific report text."""
    for key in ("final_report", "intermediate_report"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _public_status(value: str) -> str:
    """Keep the status vocabulary used by public task descriptors."""
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if normalized in _PUBLIC_STATUSES:
        return normalized
    if normalized in {"success", "completed", "done"}:
        return "succeeded"
    return "running"


def _snapshot_has_failures(snapshot: Mapping[str, Any]) -> bool:
    """Return whether the sanitized snapshot contains failed work items."""
    failures = snapshot.get("failures")
    return isinstance(failures, Sequence) and any(
        isinstance(item, Mapping)
        and item.get("status") in {"failed", "cancelled", "timed_out"}
        for item in failures
    )


def _snapshot_metadata(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bounded DeepGenome progress outside canonical report metadata."""
    return snapshot_metadata_from_mapping(
        {
            "stage": snapshot.get("report_stage"),
            "completeness": snapshot.get("report_completeness"),
            "revision": snapshot.get("report_revision"),
            "updated_at": snapshot.get("report_updated_at"),
            "progress": snapshot.get("progress", {}),
            "degraded": snapshot.get("degraded") is True,
            "failure_count": len(snapshot.get("failures", [])),
        }
    )


def _public_metadata(
    existing_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Preserve only known scalar metadata from a stored formatted result."""
    formatted = _mapping(existing_result).get("formatted")
    source = _mapping(formatted).get("metadata")
    if not isinstance(source, Mapping):
        return {}
    return {
        key: value
        for key in _SAFE_METADATA_KEYS
        if (value := source.get(key)) is not None and _is_safe_scalar(value)
    }


def _existing_follow_up_questions(
    existing_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Preserve safe follow-up strings from a stored formatted result."""
    formatted = _mapping(existing_result).get("formatted")
    values = _mapping(formatted).get("follow_up_questions")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _existing_references(
    existing_result: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    """Preserve citation-shaped scalar references without raw payloads."""
    formatted = _mapping(existing_result).get("formatted")
    values = _mapping(formatted).get("references")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(
        {
            key: item[key]
            for key in _SAFE_REFERENCE_KEYS
            if key in item and _is_safe_scalar(item[key])
        }
        for value in values
        if isinstance(value, Mapping)
        for item in (value,)
    )


def _existing_tabular(
    existing_result: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Preserve scalar table cells from a stored formatted result."""
    formatted = _mapping(existing_result).get("formatted")
    table = _mapping(formatted).get("tabular")
    if not isinstance(table, Mapping):
        return None
    headers = table.get("headers")
    rows = table.get("rows")
    if not isinstance(headers, list) or not all(
        _is_safe_scalar(value) for value in headers
    ):
        return None
    if not isinstance(rows, list) or not all(
        isinstance(row, list) and all(_is_safe_scalar(value) for value in row)
        for row in rows
    ):
        return None
    return {"headers": list(headers), "rows": [list(row) for row in rows]}


def _public_artifacts(
    existing_result: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    """Preserve only explicit artifact descriptor fields."""
    raw = _mapping(existing_result)
    execution = _mapping(raw.get("execution"))
    values = execution.get("artifacts", raw.get("artifacts"))
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    artifacts: list[Mapping[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = {
            key: value[key]
            for key in PUBLIC_ARTIFACT_KEYS
            if key in value and _is_safe_scalar(value[key])
        }
        if item:
            artifacts.append(item)
    return tuple(artifacts)


def _public_output_dirs(
    existing_result: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Preserve owner-scoped output references from old and new envelopes."""
    raw = _mapping(existing_result)
    execution = _mapping(raw.get("execution"))
    values: list[Any] = []
    for key in ("output_dirs",):
        source = execution.get(key, raw.get(key))
        if isinstance(source, Sequence) and not isinstance(
            source, (str, bytes)
        ):
            values.extend(source)
        elif source is not None:
            values.append(source)
    for key in ("task_results", "live_status"):
        source = raw.get(key)
        if isinstance(source, Sequence) and not isinstance(
            source, (str, bytes)
        ):
            values.extend(
                item.get("output_dir")
                for item in source
                if isinstance(item, Mapping)
            )
    return tuple(
        dict.fromkeys(
            value for value in values if isinstance(value, str) and value
        )
    )


def _append_warning(
    warnings: list[ExecutionWarning], warning: ExecutionWarning
) -> None:
    """Append one warning code only once."""
    if not any(item.code == warning.code for item in warnings):
        warnings.append(warning)


def _append_diagnostic(
    diagnostics: list[Mapping[str, Any]], diagnostic: Mapping[str, Any]
) -> None:
    """Append one diagnostic code only once."""
    if not any(
        item.get("code") == diagnostic.get("code") for item in diagnostics
    ):
        diagnostics.append(diagnostic)


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping view for optional persisted values."""
    return value if isinstance(value, Mapping) else {}


def _is_safe_scalar(value: Any) -> bool:
    """Return whether one value can cross the formatted public boundary."""
    return isinstance(value, (str, int, float, bool)) or value is None


def _json_compatible(value: Any) -> Any:
    """Normalize tuples and immutable mappings to JSON-compatible values."""
    return json.loads(json.dumps(value))


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
