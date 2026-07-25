# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical lifecycle validation for public HTTP agent.run responses."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NotRequired, TypedDict, Unpack

from ..agents.shared.a2ui import (
    A2uiSurfaceValidationError,
    project_review_confirm,
    validate_a2ui_surface,
)
from ..contracts.deep_genome import (
    DEEP_GENOME_FINAL_FAILURE_REASONS,
    DEEP_GENOME_PROGRESS_FIELDS,
    sanitize_nonnegative_int,
)

__all__ = [
    "LifecycleInvariantError",
    "SafeApiError",
    "SafeErrorCode",
    "build_agent_run_response",
    "canonicalize_agent_run_body",
    "canonicalize_run_record",
    "empty_agent_result",
]


class SafeErrorCode(StrEnum):
    """Stable public-safe lifecycle and transport error codes."""

    RUN_PERSISTENCE_FAILED = "run_persistence_failed"
    RUNNING_WITHOUT_WORK = "running_without_work"
    SUCCEEDED_WITHOUT_PERSISTENCE = "succeeded_without_persistence"
    INPUT_REQUIRED_WITHOUT_SURFACE = "input_required_without_surface"
    PROJECTION_FAILED = "projection_failed"
    A2UI_ACTION_CONFLICT = "a2ui_action_conflict"
    CHECKPOINT_NOT_AVAILABLE = "checkpoint_not_available"
    ROUTING_CONTRACT_VIOLATION = "routing_contract_violation"
    UPSTREAM_FAILED = "upstream_failed"
    UPSTREAM_TIMEOUT = "upstream_timeout"


class LifecycleInvariantError(RuntimeError):
    """Raised before an invalid run response reaches a public boundary."""

    def __init__(self, code: SafeErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class SafeApiError(RuntimeError):
    """One public-safe API failure routed through the factory handlers."""

    status_code: int
    code: str
    message: str
    stage: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.code)


@dataclass(frozen=True, slots=True)
class _AgentRunResponseInput:
    """Validated input fields for one public ``agent.run`` response."""

    run_id: str | None
    agent: str
    status: str
    task_ids: Sequence[str]
    options: _AgentRunResponseOptions


@dataclass(frozen=True, slots=True)
class _AgentRunResponseOptions:
    """Response options grouped to keep lifecycle input compact."""

    result: Mapping[str, Any]
    persisted: bool
    degraded_tracking: bool = False
    include_run_id: bool = True


class _AgentRunResponseKeywordOptions(TypedDict):
    """Public keyword values accepted by the lifecycle builder."""

    result: Mapping[str, Any]
    persisted: bool
    degraded_tracking: NotRequired[bool]
    include_run_id: NotRequired[bool]


_REQUIRED_RESPONSE_OPTIONS = frozenset({"result", "persisted"})
_OPTIONAL_RESPONSE_OPTIONS = frozenset({"degraded_tracking", "include_run_id"})
_SUPPORTED_RESPONSE_OPTIONS = (
    _REQUIRED_RESPONSE_OPTIONS | _OPTIONAL_RESPONSE_OPTIONS
)
_REVIEW_SUMMARY_FIELDS = ("summary", "draft", "text", "content")
_PUBLIC_RUN_HISTORY_FIELDS = (
    "agent",
    "origin",
    "user_id",
    "status",
    "created_at",
    "updated_at",
    "expires_at",
    "dialogue_id",
    "query",
    "tool_name",
    "model",
    "a2a_task_id",
    "a2a_context_id",
    "a2a_message_id",
)
_DEEP_GENOME_PRIVATE_DEBUG_FIELDS = (
    "task_results",
    "live_status",
    "artifacts",
)
_DEEP_GENOME_FAILURE_MESSAGES = {
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
    "timed_out": "analysis task timed out",
}
_DEEP_GENOME_GENERATED_REASON = re.compile(
    r"^[0-9]+ of 12 optional analyses unavailable$"
)
_DEEP_GENOME_REASON_FALLBACK = "analysis results are partially unavailable"


def empty_agent_result(*, degraded: bool = False) -> dict[str, Any]:
    """Return the smallest canonical scientific/execution projection."""
    warnings: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    if degraded:
        warnings = [
            {
                "code": "run_registry_unavailable",
                "retryable": False,
            }
        ]
    return {
        "formatted": {
            "answer": "",
            "follow_up_questions": [],
            "references": [],
            "tabular": {},
            "metadata": {},
        },
        "execution": {
            "tracking": {"degraded": degraded},
            "warnings": warnings,
            "tasks": tasks,
            "artifacts": [],
            "output_dirs": [],
            "report": None,
            "diagnostics": [],
        },
    }


def _validated_task_ids(task_ids: Sequence[str]) -> list[str]:
    values = [task_id for task_id in task_ids if task_id.strip()]
    if len(values) != len(task_ids):
        raise LifecycleInvariantError(SafeErrorCode.RUNNING_WITHOUT_WORK)
    return list(dict.fromkeys(values))


def _validate_input_required(result: Mapping[str, Any]) -> None:
    interrupt = result.get("interrupt")
    draft = interrupt.get("draft") if isinstance(interrupt, Mapping) else None
    surface = draft.get("a2ui") if isinstance(draft, Mapping) else None
    if not isinstance(surface, Mapping):
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        )
    try:
        validate_a2ui_surface(surface)
    except A2uiSurfaceValidationError as exc:
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        ) from exc


def _validate_result_projection(result: Mapping[str, Any]) -> None:
    formatted = result.get("formatted")
    execution = result.get("execution")
    if not isinstance(formatted, Mapping) or not isinstance(
        execution, Mapping
    ):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    required_formatted = {
        "answer",
        "follow_up_questions",
        "references",
        "tabular",
        "metadata",
    }
    required_execution = {
        "tracking",
        "warnings",
        "tasks",
        "artifacts",
        "output_dirs",
        "report",
        "diagnostics",
    }
    if not required_formatted.issubset(formatted):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    if not required_execution.issubset(execution):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)


def build_agent_run_response(
    *,
    run_id: str | None,
    agent: str,
    status: str,
    task_ids: Sequence[str],
    **options: Unpack[_AgentRunResponseKeywordOptions],
) -> dict[str, Any]:
    """Validate and serialize one canonical public agent.run."""
    _validate_response_options(options)
    request = _AgentRunResponseInput(
        run_id=run_id,
        agent=agent,
        status=status,
        task_ids=task_ids,
        options=_AgentRunResponseOptions(
            result=options["result"],
            persisted=options["persisted"],
            degraded_tracking=options.get("degraded_tracking", False),
            include_run_id=options.get("include_run_id", True),
        ),
    )
    normalized_id = (
        request.run_id if request.run_id and request.run_id.strip() else None
    )
    normalized_tasks = _validated_task_ids(request.task_ids)
    if request.status == "running":
        recoverable = normalized_id is not None and request.options.persisted
        degraded = (
            normalized_id is None
            and bool(normalized_tasks)
            and request.options.degraded_tracking
        )
        if not (recoverable or degraded):
            raise LifecycleInvariantError(SafeErrorCode.RUNNING_WITHOUT_WORK)
    if request.status == "succeeded" and (
        normalized_id is None or not request.options.persisted
    ):
        raise LifecycleInvariantError(
            SafeErrorCode.SUCCEEDED_WITHOUT_PERSISTENCE
        )
    if request.status == "succeeded":
        _validate_result_projection(request.options.result)
    if request.status == "input_required":
        if normalized_id is None or not request.options.persisted:
            raise LifecycleInvariantError(
                SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
            )
        _validate_input_required(request.options.result)
    body: dict[str, Any] = {
        "id": normalized_id,
        "object": "agent.run",
        "agent": request.agent,
        "status": request.status,
        "task_ids": normalized_tasks,
        "result": deepcopy(dict(request.options.result)),
    }
    if normalized_id is not None and request.options.include_run_id:
        body["run_id"] = normalized_id
    if request.options.degraded_tracking:
        body["degraded_tracking"] = True
    return body


def _validate_response_options(
    options: Mapping[str, Any],
) -> None:
    """Reject incomplete or unsupported builder options before projection."""
    missing = sorted(_REQUIRED_RESPONSE_OPTIONS - options.keys())
    if missing:
        names = ", ".join(repr(name) for name in missing)
        raise TypeError(
            "build_agent_run_response() missing required keyword-only "
            f"argument(s): {names}"
        )
    unknown = sorted(options.keys() - _SUPPORTED_RESPONSE_OPTIONS)
    if unknown:
        names = ", ".join(repr(name) for name in unknown)
        raise TypeError(
            "build_agent_run_response() got unexpected keyword "
            f"argument(s): {names}"
        )


def _task_ids_from_result(result: Mapping[str, Any]) -> tuple[str, ...]:
    """Recover accepted task ids from the response payload when needed."""
    execution = result.get("execution")
    if isinstance(execution, Mapping):
        tasks = execution.get("tasks")
        if isinstance(tasks, list):
            values = tuple(
                str(task_id)
                for task in tasks
                for task_id in [task.get("id")]
                if isinstance(task, Mapping)
                and isinstance(task_id, str)
                and task_id.strip()
            )
            if values:
                return values
    formatted = result.get("formatted")
    if not isinstance(formatted, Mapping):
        return ()
    metadata = formatted.get("metadata")
    if not isinstance(metadata, Mapping):
        return ()
    raw_task_ids = metadata.get("task_ids")
    if isinstance(raw_task_ids, (list, tuple)):
        values = tuple(
            value
            for item in raw_task_ids
            for value in [item.strip() if isinstance(item, str) else ""]
            if value
        )
        if values:
            return values
    task_id = metadata.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return (task_id.strip(),)
    return ()


def _ensure_review_interrupt_surface(
    interrupt: Mapping[str, Any],
    *,
    run_id: str | None,
) -> dict[str, Any]:
    """Return the safe Review interrupt with a valid deterministic surface."""
    draft = interrupt.get("draft")
    surface: Mapping[str, Any] | None = None
    if isinstance(draft, Mapping):
        existing = draft.get("a2ui")
        if isinstance(existing, Mapping):
            try:
                validate_a2ui_surface(existing)
            except A2uiSurfaceValidationError:
                pass
            else:
                surface = existing
    summary = _safe_review_summary(draft)
    if surface is None:
        surface = project_review_confirm(summary)
        if run_id is not None:
            surface = {**surface, "surface_id": f"{run_id}-review-confirm"}
    try:
        validate_a2ui_surface(surface)
    except A2uiSurfaceValidationError as exc:
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        ) from exc
    projected: dict[str, Any] = {
        "draft": {"summary": summary, "a2ui": dict(surface)}
    }
    thread_id = interrupt.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        projected["thread_id"] = thread_id
    return projected


def _safe_review_summary(draft: Any) -> str:
    """Extract a review summary without rendering arbitrary stored data."""
    if isinstance(draft, Mapping):
        for field in _REVIEW_SUMMARY_FIELDS:
            value = draft.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "Review approval required."


def canonicalize_run_record(
    record: Mapping[str, Any], *, debug: bool = False
) -> dict[str, Any]:
    """Validate one persisted record while preserving its history fields."""
    run_id, _ = _normalize_run_identity(record)
    if run_id is None:
        raise LifecycleInvariantError(SafeErrorCode.ROUTING_CONTRACT_VIOLATION)
    source = {**dict(record), "id": run_id, "run_id": run_id}
    if record.get("status") == "running" and record.get("result") is None:
        source["result"] = empty_agent_result(
            degraded=record.get("degraded_tracking") is True
        )
    canonical = canonicalize_agent_run_body(source)
    projected = _project_scalar_fields(record, _PUBLIC_RUN_HISTORY_FIELDS)
    projected["id"] = run_id
    projected["run_id"] = run_id
    if canonical["status"] == "input_required":
        projected["result"] = {"interrupt": canonical["interrupt"]}
    else:
        projected["result"] = canonical["result"]
        formatted = canonical["result"].get("formatted")
        if isinstance(formatted, Mapping):
            answer = formatted.get("answer")
            if isinstance(answer, str):
                projected["answer"] = answer
        if record.get("agent") == "deep_genome":
            _merge_deep_genome_snapshot(
                projected,
                source_result=record.get("result"),
                debug=debug,
            )
    projected["task_ids"] = canonical["task_ids"]
    if canonical["status"] == "failed":
        projected["error"] = "run failed"
    else:
        projected.pop("error", None)
    if canonical.get("degraded_tracking"):
        projected["degraded_tracking"] = True
    return projected


def _merge_deep_genome_snapshot(
    projected: dict[str, Any],
    *,
    source_result: Any,
    debug: bool,
) -> None:
    """Restore the already-sanitized DeepGenome read projection safely."""
    if not isinstance(source_result, Mapping):
        return
    snapshot = _project_deep_genome_snapshot(source_result)
    if snapshot is None:
        return
    result = projected.get("result")
    if not isinstance(result, dict):
        return
    result.update(snapshot)
    formatted_source = source_result.get("formatted")
    formatted = result.get("formatted")
    if isinstance(formatted_source, Mapping) and isinstance(formatted, dict):
        metadata = formatted.get("metadata")
        if isinstance(metadata, dict):
            metadata["report"] = _deep_genome_report_metadata(snapshot)
    else:
        result.pop("formatted", None)
        result.pop("execution", None)
    if debug:
        for field in _DEEP_GENOME_PRIVATE_DEBUG_FIELDS:
            value = source_result.get(field)
            if value is not None:
                result[field] = deepcopy(value)
    best_report = snapshot["final_report"] or snapshot["intermediate_report"]
    if isinstance(best_report, str) and best_report.strip():
        projected["answer"] = best_report


def _project_deep_genome_snapshot(
    source: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Allowlist the persisted DeepGenome snapshot after canonicalization."""
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
    return {
        "intermediate_report": _optional_string(
            source.get("intermediate_report")
        ),
        "final_report": _optional_string(source.get("final_report")),
        "report_stage": stage,
        "report_completeness": completeness,
        "report_revision": sanitize_nonnegative_int(revision),
        "report_updated_at": _optional_string(source.get("report_updated_at")),
        "progress": _project_deep_genome_progress(source.get("progress")),
        "degraded": source.get("degraded") is True,
        "degraded_reason": _project_deep_genome_reason(
            source.get("degraded_reason")
        ),
        "failures": _project_deep_genome_failures(source.get("failures")),
    }


def _optional_string(value: Any) -> str | None:
    """Keep public text or null; reject non-string persisted values."""
    return value if isinstance(value, str) else None


def _project_deep_genome_progress(value: Any) -> dict[str, int | bool | str]:
    """Project the stable ordered progress fields without arbitrary keys."""
    source = value if isinstance(value, Mapping) else {}
    planning_complete = source.get("planning_complete") is True
    brief_gene_status = source.get("brief_gene_status")
    progress: dict[str, int | bool | str] = {
        "planning_complete": planning_complete,
        "brief_gene_status": (
            brief_gene_status.strip().lower()
            if isinstance(brief_gene_status, str) and brief_gene_status.strip()
            else "unknown"
        ),
    }
    for key in DEEP_GENOME_PROGRESS_FIELDS[2:]:
        progress[key] = sanitize_nonnegative_int(source.get(key))
    return progress


def _project_deep_genome_reason(value: Any) -> str | None:
    """Keep only fixed public degraded reasons from the snapshot contract."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if _DEEP_GENOME_GENERATED_REASON.fullmatch(normalized):
        return normalized
    if normalized in DEEP_GENOME_FINAL_FAILURE_REASONS:
        return normalized
    return _DEEP_GENOME_REASON_FALLBACK


def _project_deep_genome_failures(value: Any) -> list[dict[str, str]]:
    """Project fixed failure text rather than persisted provider details."""
    if not isinstance(value, list):
        return []
    projected: list[dict[str, str]] = []
    valid_statuses = {"succeeded", "failed", "cancelled", "timed_out"}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        work_item_key = item.get("work_item_key")
        status = item.get("status")
        if (
            not isinstance(work_item_key, str)
            or not work_item_key.strip()
            or not isinstance(status, str)
            or status not in valid_statuses
        ):
            continue
        projected.append(
            {
                "work_item_key": work_item_key.strip(),
                "status": status,
                "message": _DEEP_GENOME_FAILURE_MESSAGES.get(
                    status, "analysis task unavailable"
                ),
            }
        )
    return projected


def _deep_genome_report_metadata(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the documented additive metadata from the public snapshot."""
    return {
        "stage": snapshot["report_stage"],
        "completeness": snapshot["report_completeness"],
        "revision": snapshot["report_revision"],
        "updated_at": snapshot["report_updated_at"],
        "progress": snapshot["progress"],
        "degraded": snapshot["degraded"],
        "failure_count": len(snapshot["failures"]),
    }


def _normalize_run_identity(
    body: Mapping[str, Any],
) -> tuple[str | None, bool]:
    """Resolve the canonical run id and whether the alias should be emitted."""
    body_id = body.get("id")
    normalized_id = (
        body_id if isinstance(body_id, str) and body_id.strip() else None
    )
    legacy_run_id = body.get("run_id")
    normalized_run_id = (
        legacy_run_id
        if isinstance(legacy_run_id, str) and legacy_run_id.strip()
        else None
    )
    if (
        normalized_id is not None
        and normalized_run_id is not None
        and normalized_id != normalized_run_id
    ):
        raise LifecycleInvariantError(SafeErrorCode.ROUTING_CONTRACT_VIOLATION)
    if normalized_id is None:
        normalized_id = normalized_run_id
    return normalized_id, normalized_run_id is not None


def canonicalize_agent_run_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one public ``agent.run`` body at the HTTP boundary."""
    status = body.get("status")
    agent = body.get("agent")
    if not isinstance(status, str) or not isinstance(agent, str):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    run_id, include_run_id = _normalize_run_identity(body)
    raw_task_ids = body.get("task_ids")
    task_ids = (
        tuple(item for item in raw_task_ids if isinstance(item, str))
        if isinstance(raw_task_ids, list)
        else ()
    )
    degraded_tracking = body.get("degraded_tracking") is True
    if status == "input_required":
        return _canonicalize_input_required(
            body,
            request=_AgentRunResponseInput(
                run_id=run_id,
                agent=agent,
                status=status,
                task_ids=task_ids,
                options=_AgentRunResponseOptions(
                    result={},
                    persisted=run_id is not None,
                    degraded_tracking=degraded_tracking,
                    include_run_id=include_run_id,
                ),
            ),
        )
    result = body.get("result")
    if result is None and status in {"succeeded", "failed"}:
        result = {}
    if not isinstance(result, Mapping):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    if not task_ids:
        task_ids = _task_ids_from_result(result)
    return build_agent_run_response(
        run_id=run_id,
        agent=agent,
        status=status,
        task_ids=task_ids,
        result=_canonicalize_result_projection(
            result,
            degraded_tracking=degraded_tracking,
            task_ids=task_ids,
        ),
        persisted=run_id is not None,
        degraded_tracking=degraded_tracking,
        include_run_id=include_run_id,
    )


def _canonicalize_input_required(
    body: Mapping[str, Any],
    *,
    request: _AgentRunResponseInput,
) -> dict[str, Any]:
    """Validate and preserve the legacy top-level pause representation."""
    interrupt = body.get("interrupt")
    if not isinstance(interrupt, Mapping):
        result = body.get("result")
        interrupt = (
            result.get("interrupt") if isinstance(result, Mapping) else None
        )
    if not isinstance(interrupt, Mapping):
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        )
    projected_interrupt = (
        _ensure_review_interrupt_surface(interrupt, run_id=request.run_id)
        if request.agent == "review"
        else _project_interrupt_surface(interrupt)
    )
    validated = build_agent_run_response(
        run_id=request.run_id,
        agent=request.agent,
        status="input_required",
        task_ids=request.task_ids,
        result={"interrupt": projected_interrupt},
        persisted=request.options.persisted,
        degraded_tracking=request.options.degraded_tracking,
        include_run_id=request.options.include_run_id,
    )
    validated["interrupt"] = projected_interrupt
    validated.pop("result", None)
    return validated


def _canonicalize_result_projection(
    result: Mapping[str, Any],
    *,
    degraded_tracking: bool,
    task_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Lift partial terminal results into the safe canonical projection."""
    canonical = empty_agent_result(degraded=degraded_tracking)
    formatted = result.get("formatted")
    merged_formatted = _project_formatted(
        formatted if isinstance(formatted, Mapping) else {},
        canonical["formatted"],
    )
    execution = result.get("execution")
    merged_execution = _project_execution(
        execution if isinstance(execution, Mapping) else {},
        canonical["execution"],
    )
    if task_ids and not merged_execution.get("tasks"):
        merged_execution["tasks"] = [
            {"id": task_id, "accepted": True} for task_id in task_ids
        ]
    projected: dict[str, Any] = {
        "formatted": merged_formatted,
        "execution": merged_execution,
    }
    submitted_surface = _project_a2ui_result(result.get("a2ui"))
    if submitted_surface is not None:
        projected["a2ui"] = submitted_surface
    return projected


def _project_a2ui_result(value: Any) -> dict[str, Any] | None:
    """Validate a downlink or the bounded submitted-value variant."""
    if not isinstance(value, Mapping):
        return None
    props = value.get("props")
    if not isinstance(props, Mapping):
        return None
    submitted_keys = {"status", "accepted", "cancelled", "fields", "selected"}
    base = {
        **dict(value),
        "props": {
            key: item
            for key, item in props.items()
            if key not in submitted_keys
        },
    }
    try:
        validated = validate_a2ui_surface(base)
    except A2uiSurfaceValidationError:
        return None
    projected = validated.model_dump()
    if props.get("status") != "submitted":
        return projected
    projected_props = dict(projected["props"])
    projected_props["status"] = "submitted"
    for key in ("accepted", "cancelled"):
        item = props.get(key)
        if isinstance(item, bool):
            projected_props[key] = item
    fields = props.get("fields")
    if isinstance(fields, Mapping):
        projected_props["fields"] = dict(fields)
    selected = props.get("selected")
    if isinstance(selected, str) or (
        isinstance(selected, list)
        and all(isinstance(item, str) for item in selected)
    ):
        projected_props["selected"] = selected
    projected["props"] = projected_props
    return projected


_METADATA_SCALAR_KEYS = frozenset(
    {
        "query",
        "original_query",
        "species",
        "species_code",
        "gene",
        "gene_id",
        "resolved_gene_id",
        "resolved_to_id",
        "resolved_species_code",
        "resolve_gene_id",
        "resolve_to_id",
        "consumer",
        "degraded",
        "degraded_tracking",
        "status",
    }
)
_REFERENCE_KEYS = (
    "file_id",
    "title",
    "au",
    "ti",
    "so",
    "vl",
    "bp",
    "ep",
    "py",
    "di",
    "dl",
    "pm",
)


def _is_safe_scalar(value: Any) -> bool:
    """Return whether a value can cross the public lifecycle boundary."""
    return isinstance(value, (str, int, float, bool)) or value is None


def _project_scalar_fields(
    source: Mapping[str, Any], keys: Iterable[str]
) -> dict[str, Any]:
    """Copy only explicit scalar fields from an untrusted mapping."""
    return {
        key: source[key]
        for key in keys
        if key in source and _is_safe_scalar(source[key])
    }


def _project_record_list(
    value: Any, keys: Sequence[str]
) -> list[dict[str, Any]]:
    """Project a list of records with no arbitrary nested values."""
    if not isinstance(value, list):
        return []
    return [
        _project_scalar_fields(item, keys)
        for item in value
        if isinstance(item, Mapping)
    ]


def _project_string_list(value: Any) -> list[str]:
    """Keep only public string values from an untrusted list."""
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _project_tabular(value: Any) -> dict[str, Any]:
    """Preserve table headers and scalar rows without nested records."""
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {}
    headers = value.get("headers")
    if isinstance(headers, list) and all(
        _is_safe_scalar(item) for item in headers
    ):
        projected["headers"] = list(headers)
    rows = value.get("rows")
    if isinstance(rows, list) and all(
        isinstance(row, list) and all(_is_safe_scalar(cell) for cell in row)
        for row in rows
    ):
        projected["rows"] = [list(row) for row in rows]
    return projected


def _project_formatted(
    formatted: Mapping[str, Any], defaults: Mapping[str, Any]
) -> dict[str, Any]:
    """Project the display envelope through its explicit public allowlist."""
    answer = formatted.get("answer")
    follow_up = formatted.get("follow_up_questions")
    metadata = formatted.get("metadata")
    return {
        "answer": answer if isinstance(answer, str) else defaults["answer"],
        "follow_up_questions": _project_string_list(follow_up),
        "references": _project_record_list(
            formatted.get("references"), _REFERENCE_KEYS
        ),
        "tabular": _project_tabular(formatted.get("tabular")),
        "metadata": (
            _project_scalar_fields(metadata, _METADATA_SCALAR_KEYS)
            if isinstance(metadata, Mapping)
            else {}
        ),
    }


def _project_execution(
    execution: Mapping[str, Any], defaults: Mapping[str, Any]
) -> dict[str, Any]:
    """Project lifecycle execution metadata through nested allowlists."""
    tracking = execution.get("tracking")
    degraded = (
        tracking.get("degraded")
        if isinstance(tracking, Mapping)
        and isinstance(tracking.get("degraded"), bool)
        else defaults["tracking"]["degraded"]
    )
    report = execution.get("report")
    return {
        "tracking": {"degraded": degraded},
        "warnings": _project_record_list(
            execution.get("warnings"),
            ("code", "stage", "retryable", "count", "rejected_count"),
        ),
        "tasks": _project_record_list(
            execution.get("tasks"),
            ("id", "accepted", "status", "output_dir"),
        ),
        "artifacts": _project_record_list(
            execution.get("artifacts"),
            (
                "id",
                "role",
                "name",
                "mime_type",
                "size_bytes",
                "output_dir",
                "uri",
            ),
        ),
        "output_dirs": _project_string_list(execution.get("output_dirs")),
        "report": (
            _project_scalar_fields(
                report,
                (
                    "role",
                    "state",
                    "artifact_id",
                    "mime_type",
                    "size_bytes",
                    "output_dir",
                    "uri",
                ),
            )
            if isinstance(report, Mapping)
            else None
        ),
        "diagnostics": _project_record_list(
            execution.get("diagnostics"), ("code", "stage", "retryable")
        ),
    }


def _project_interrupt_surface(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only a nonblank thread id and a validated generic A2UI draft."""
    draft = interrupt.get("draft")
    surface = draft.get("a2ui") if isinstance(draft, Mapping) else None
    if not isinstance(surface, Mapping):
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        )
    try:
        validate_a2ui_surface(surface)
    except A2uiSurfaceValidationError as exc:
        raise LifecycleInvariantError(
            SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
        ) from exc
    projected: dict[str, Any] = {"draft": {"a2ui": dict(surface)}}
    thread_id = interrupt.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        projected["thread_id"] = thread_id
    return projected
