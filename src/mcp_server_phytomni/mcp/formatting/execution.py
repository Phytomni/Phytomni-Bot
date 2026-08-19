# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical operational execution and compatibility projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from typing import Any, Literal, cast

from .models import (
    ExecutionProjection,
    ExecutionWarning,
    FormattedToolResult,
    ReportExecution,
)

_REPORT_STATES = frozenset({"none", "intermediate", "final", "degraded"})
_ReportState = Literal["none", "intermediate", "final", "degraded"]
_OPERATIONAL_METADATA_KEYS = frozenset(
    {
        "analysis_id",
        "artifacts",
        "compute_resource",
        "error",
        "failures",
        "live_status",
        "log_status",
        "output_dir",
        "status",
        "succeeded_count",
        "failed_count",
        "task_id",
        "task_ids",
    }
)
_TASK_KEYS = frozenset({"accepted", "id", "status", "kind", "error_code"})
PUBLIC_ARTIFACT_KEYS = (
    "id",
    "role",
    "name",
    "media_type",
    "downloadable",
    "report_context_eligible",
    "mime_type",
    "size_bytes",
    "output_dir",
    "uri",
    "download_ref",
)
_ARTIFACT_KEYS = frozenset(PUBLIC_ARTIFACT_KEYS)


def apply_compatibility_projection(
    formatted: FormattedToolResult,
    execution: ExecutionProjection,
) -> FormattedToolResult:
    """Derive temporary formatted compatibility fields from execution."""
    metadata = {
        key: value
        for key, value in formatted.metadata.items()
        if key not in _OPERATIONAL_METADATA_KEYS
    }
    if execution.report is not None:
        metadata["report"] = asdict(execution.report)
    return replace(
        formatted,
        metadata=metadata,
        output_dirs=execution.output_dirs,
    )


def build_execution_projection(
    tool_name: str,
    normalized_raw: Any,
) -> ExecutionProjection:
    """Extract only known operational fields from one raw tool payload."""
    del tool_name
    raw = _mapping(normalized_raw)
    nested = _mapping(raw.get("execution"))
    artifacts = _safe_artifacts(raw, nested)
    return ExecutionProjection(
        tracking=_safe_tracking(raw, nested),
        warnings=_safe_warnings(raw, nested),
        tasks=_safe_tasks(raw, nested),
        artifacts=artifacts,
        output_dirs=_safe_output_dirs(raw, nested),
        report=_safe_report(raw, nested, len(artifacts)),
        diagnostics=_safe_diagnostics(raw, nested),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _safe_tracking(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> Mapping[str, Any]:
    tracking = _mapping(nested.get("tracking"))
    degraded = (
        tracking.get("degraded") is True
        or nested.get("degraded_tracking") is True
        or raw.get("degraded_tracking") is True
        or raw.get("degraded") is True
    )
    return {"degraded": degraded}


def _safe_warnings(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> tuple[ExecutionWarning, ...]:
    source = nested.get("warnings")
    if source is None:
        source = raw.get("submission_warnings", raw.get("warnings"))
    values = [source] if isinstance(source, Mapping) else source
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    warnings: list[ExecutionWarning] = []
    for value in values:
        item = _mapping(value)
        code = _string(item.get("code"))
        if code is None:
            continue
        stage = _string(item.get("stage"))
        warnings.append(
            ExecutionWarning(
                code=code,
                retryable=item.get("retryable") is True,
                stage=stage,
            )
        )
    return tuple(warnings)


def _safe_tasks(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    source = nested.get("tasks", raw.get("tasks"))
    candidates: list[Mapping[str, Any]] = []
    if isinstance(source, Mapping):
        candidates.extend(
            item for item in source.values() if isinstance(item, Mapping)
        )
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        candidates.extend(item for item in source if isinstance(item, Mapping))

    if not candidates:
        raw_task_ids = raw.get("task_ids")
        if isinstance(raw_task_ids, Mapping):
            candidates.extend({"id": value} for value in raw_task_ids.values())
        elif isinstance(raw_task_ids, Sequence) and not isinstance(
            raw_task_ids, (str, bytes)
        ):
            candidates.extend({"id": value} for value in raw_task_ids)
        elif raw.get("task_id") is not None:
            candidates.append(raw)

    tasks: list[Mapping[str, Any]] = []
    for item in candidates:
        task_id = _string(item.get("id", item.get("task_id")))
        if task_id is None:
            continue
        status = _string(item.get("status"))
        descriptor: dict[str, Any] = {
            "id": task_id,
            "accepted": (
                item["accepted"]
                if isinstance(item.get("accepted"), bool)
                else True
            ),
        }
        if status is not None:
            descriptor["status"] = status
        if "kind" in item:
            kind = item.get("kind")
            descriptor["kind"] = kind if isinstance(kind, str) else ""
        if "error_code" in item:
            error_code = item.get("error_code")
            descriptor["error_code"] = (
                error_code if isinstance(error_code, str) else None
            )
        tasks.append(
            {key: descriptor[key] for key in _TASK_KEYS if key in descriptor}
        )
    return tuple(tasks)


def _safe_artifacts(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    source = nested.get("artifacts", raw.get("artifacts"))
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        return ()
    artifacts: list[Mapping[str, Any]] = []
    for value in source:
        item = _mapping(value)
        artifact_id = _string(item.get("id", item.get("artifact_id")))
        role = _string(item.get("role"))
        name = _string(item.get("name"))
        size_bytes = _nonnegative_int(item.get("size_bytes"))
        descriptor: dict[str, Any] = {}
        if artifact_id is not None:
            descriptor["id"] = artifact_id
        if name is not None:
            descriptor["name"] = name
        if role is not None:
            descriptor["role"] = role
        if size_bytes is not None:
            descriptor["size_bytes"] = size_bytes
        if descriptor:
            artifacts.append(
                {
                    key: descriptor[key]
                    for key in _ARTIFACT_KEYS
                    if key in descriptor
                }
            )
    return tuple(artifacts)


def _safe_output_dirs(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> tuple[str, ...]:
    values: list[Any] = []
    source = nested.get("output_dirs", raw.get("output_dirs"))
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        values.extend(source)
    elif source is not None:
        values.append(source)
    design_tasks = raw.get("design_task_result")
    if isinstance(design_tasks, Sequence) and not isinstance(
        design_tasks, (str, bytes)
    ):
        values.extend(
            item.get("output_dir")
            for item in design_tasks
            if isinstance(item, Mapping)
        )
    return tuple(
        value for value in (_string(item) for item in values) if value
    )


def _safe_report(
    raw: Mapping[str, Any],
    nested: Mapping[str, Any],
    artifact_count: int,
) -> ReportExecution | None:
    source = _mapping(nested.get("report", raw.get("report")))
    raw_state = _string(source.get("state"))
    state = (
        cast(_ReportState, raw_state) if raw_state in _REPORT_STATES else None
    )
    if state is None:
        stage = _string(raw.get("report_stage"))
        if stage in {"intermediate", "final"}:
            state = cast(_ReportState, stage)
        elif raw.get("degraded") is True:
            state = "degraded"
    degraded = source.get("degraded") is True or raw.get("degraded") is True
    count = _nonnegative_int(source.get("source_artifact_count"))
    if count is None:
        count = artifact_count
    if state is None and not degraded and count == 0:
        return None
    return ReportExecution(
        state=state or ("degraded" if degraded else "none"),
        degraded=degraded,
        source_artifact_count=count,
    )


def _safe_diagnostics(
    raw: Mapping[str, Any], nested: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    source = nested.get("diagnostics", raw.get("diagnostics"))
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes)):
        return ()
    diagnostics: list[Mapping[str, Any]] = []
    for value in source:
        item = _mapping(value)
        code = _string(item.get("code"))
        if code is None:
            continue
        diagnostic: dict[str, Any] = {
            "code": code,
            "retryable": item.get("retryable") is True,
        }
        stage = _string(item.get("stage"))
        if stage is not None:
            diagnostic["stage"] = stage
        diagnostics.append(diagnostic)
    return tuple(diagnostics)


__all__ = [
    "ExecutionProjection",
    "ExecutionWarning",
    "ReportExecution",
    "PUBLIC_ARTIFACT_KEYS",
    "apply_compatibility_projection",
    "build_execution_projection",
]
