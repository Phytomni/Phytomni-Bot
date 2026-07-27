# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical lifecycle validation for public HTTP agent.run responses."""

from __future__ import annotations

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
from ..agents.shared.citation_enrichment import CITATION_BIBLIO_FIELDS
from ..mcp.formatting.execution import PUBLIC_ARTIFACT_KEYS
from ..runtime.deep_genome_store_projection import (
    public_snapshot_to_canonical_result,
    sanitize_deep_genome_snapshot,
    snapshot_metadata_from_mapping,
)
from ..runtime.execution_defaults import empty_execution_projection
from ..runtime.locale import SupportedLocale, message_for

__all__ = [
    "LifecycleInvariantError",
    "SafeApiError",
    "SafeErrorCode",
    "build_agent_run_response",
    "canonicalize_agent_run_body",
    "canonicalize_run_record",
    "empty_agent_result",
    "expert_safe_error",
    "run_persistence_error",
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
    ROUTING_UPSTREAM_FAILED = "routing_upstream_failed"
    SELECTED_AGENT_INVALID_ARGUMENT = "selected_agent_invalid_argument"
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


def expert_safe_error(
    code: SafeErrorCode,
    *,
    status_code: int,
    locale: SupportedLocale,
    stage: str,
    retryable: bool,
) -> SafeApiError:
    """Build one localized, request-data-free Expert error."""
    return SafeApiError(
        status_code=status_code,
        code=code.value,
        message=message_for(code.value, locale),
        stage=stage,
        retryable=retryable,
    )


def run_persistence_error() -> SafeApiError:
    """Return the stable public error for durable run persistence failures."""
    return SafeApiError(
        status_code=500,
        code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
        message="run persistence failed",
        stage="persistence",
    )


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
    "request_id",
    "a2a_task_id",
    "a2a_context_id",
    "a2a_message_id",
)
_DEEP_GENOME_PRIVATE_DEBUG_FIELDS = (
    "task_results",
    "live_status",
    "artifacts",
)


def empty_agent_result(*, degraded: bool = False) -> dict[str, Any]:
    """Return the smallest canonical scientific/execution projection."""
    result = empty_execution_projection(degraded=degraded)
    warnings: list[dict[str, Any]] = []
    if degraded:
        warnings = [
            {
                "code": "run_registry_unavailable",
                "retryable": False,
            }
        ]
    result["execution"]["warnings"] = warnings
    return result


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
    degraded_tracking = options.get("degraded_tracking", False) or (
        _execution_is_degraded(options["result"])
    )
    request = _AgentRunResponseInput(
        run_id=run_id,
        agent=agent,
        status=status,
        task_ids=task_ids,
        options=_AgentRunResponseOptions(
            result=options["result"],
            persisted=options["persisted"],
            degraded_tracking=degraded_tracking,
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


def _execution_is_degraded(result: Mapping[str, Any]) -> bool:
    """Derive the compatibility flag from the canonical execution block."""
    execution = result.get("execution")
    tracking = (
        execution.get("tracking") if isinstance(execution, Mapping) else None
    )
    return isinstance(tracking, Mapping) and tracking.get("degraded") is True


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
        projected["result"] = {
            "interrupt": canonical["interrupt"],
            "status": "input_required",
        }
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
                status=canonical["status"],
                task_ids=canonical["task_ids"],
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
    status: str,
    task_ids: Sequence[str],
) -> None:
    """Normalize legacy DeepGenome snapshots into the canonical result."""
    if not isinstance(source_result, Mapping):
        return
    snapshot = sanitize_deep_genome_snapshot(source_result)
    if snapshot is None:
        if _has_canonical_deep_genome_result(source_result):
            _copy_deep_genome_debug_fields(
                projected.get("result"), source_result, debug=debug
            )
        return
    task_id = next((item for item in task_ids if item.strip()), None)
    if task_id is None:
        task_id = _legacy_deep_genome_task_id(source_result)
    if task_id is None:
        return
    result = public_snapshot_to_canonical_result(
        snapshot,
        task_id=task_id,
        status=status,
        existing_result=source_result,
    )
    _copy_deep_genome_debug_fields(result, source_result, debug=debug)
    projected["result"] = result
    formatted = result.get("formatted")
    if isinstance(formatted, Mapping):
        answer = formatted.get("answer")
        if isinstance(answer, str):
            projected["answer"] = answer


def _has_canonical_deep_genome_result(source: Mapping[str, Any]) -> bool:
    """Return whether a result already carries the new report projection."""
    formatted = source.get("formatted")
    execution = source.get("execution")
    report = (
        execution.get("report") if isinstance(execution, Mapping) else None
    )
    return isinstance(formatted, Mapping) and isinstance(report, Mapping)


def _legacy_deep_genome_task_id(source: Mapping[str, Any]) -> str | None:
    """Recover the umbrella identity from the historical task result list."""
    for field in ("task_results", "live_status"):
        values = source.get(field)
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes)
        ):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            task_id = item.get("task_id", item.get("id"))
            if isinstance(task_id, str) and task_id.strip():
                return task_id
    return None


def _copy_deep_genome_debug_fields(
    result: Any,
    source: Mapping[str, Any],
    *,
    debug: bool,
) -> None:
    """Copy private registry fields only for explicit debug reads."""
    if not debug or not isinstance(result, dict):
        return
    for field in _DEEP_GENOME_PRIVATE_DEBUG_FIELDS:
        value = source.get(field)
        if value is not None:
            result[field] = deepcopy(value)


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
    *CITATION_BIBLIO_FIELDS,
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
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
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
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
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
    projected_metadata = (
        _project_scalar_fields(metadata, _METADATA_SCALAR_KEYS)
        if isinstance(metadata, Mapping)
        else {}
    )
    report = metadata.get("report") if isinstance(metadata, Mapping) else None
    if isinstance(report, Mapping):
        projected_metadata["report"] = _project_scalar_fields(
            report,
            ("state", "degraded", "source_artifact_count"),
        )
    deep_genome = (
        metadata.get("deep_genome") if isinstance(metadata, Mapping) else None
    )
    if isinstance(deep_genome, Mapping):
        projected_metadata["deep_genome"] = snapshot_metadata_from_mapping(
            deep_genome
        )
    return {
        "answer": answer if isinstance(answer, str) else defaults["answer"],
        "follow_up_questions": _project_string_list(follow_up),
        "references": _project_record_list(
            formatted.get("references"), _REFERENCE_KEYS
        ),
        "tabular": _project_tabular(formatted.get("tabular")),
        "metadata": projected_metadata,
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
            PUBLIC_ARTIFACT_KEYS,
        ),
        "output_dirs": _project_string_list(execution.get("output_dirs")),
        "report": (
            _project_scalar_fields(
                report,
                (
                    "role",
                    "state",
                    "degraded",
                    "source_artifact_count",
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
