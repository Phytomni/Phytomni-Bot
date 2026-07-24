# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical lifecycle validation for public HTTP agent.run responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NotRequired, TypedDict, Unpack

from ..agents.shared.a2ui import (
    A2uiSurfaceValidationError,
    project_review_confirm,
    summary_text_from_interrupt_draft,
    validate_a2ui_surface,
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
    result: Mapping[str, Any]
    persisted: bool
    degraded_tracking: bool = False
    include_run_id: bool = True


class _AgentRunResponseOptions(TypedDict):
    """Keyword-only values grouped to preserve the public call contract."""

    result: Mapping[str, Any]
    persisted: bool
    degraded_tracking: NotRequired[bool]
    include_run_id: NotRequired[bool]


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
    **options: Unpack[_AgentRunResponseOptions],
) -> dict[str, Any]:
    """Validate and serialize one canonical public agent.run."""
    request = _AgentRunResponseInput(
        run_id=run_id,
        agent=agent,
        status=status,
        task_ids=task_ids,
        result=options["result"],
        persisted=options["persisted"],
        degraded_tracking=options.get("degraded_tracking", False),
        include_run_id=options.get("include_run_id", True),
    )
    normalized_id = (
        request.run_id if request.run_id and request.run_id.strip() else None
    )
    normalized_tasks = _validated_task_ids(request.task_ids)
    if request.status == "running":
        recoverable = normalized_id is not None and request.persisted
        degraded = (
            normalized_id is None
            and bool(normalized_tasks)
            and request.degraded_tracking
        )
        if not (recoverable or degraded):
            raise LifecycleInvariantError(SafeErrorCode.RUNNING_WITHOUT_WORK)
    if request.status == "succeeded" and (
        normalized_id is None or not request.persisted
    ):
        raise LifecycleInvariantError(
            SafeErrorCode.SUCCEEDED_WITHOUT_PERSISTENCE
        )
    if request.status == "succeeded":
        _validate_result_projection(request.result)
    if request.status == "input_required":
        if normalized_id is None or not request.persisted:
            raise LifecycleInvariantError(
                SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE
            )
        _validate_input_required(request.result)
    body: dict[str, Any] = {
        "id": normalized_id,
        "object": "agent.run",
        "agent": request.agent,
        "status": request.status,
        "task_ids": normalized_tasks,
        "result": deepcopy(dict(request.result)),
    }
    if normalized_id is not None and request.include_run_id:
        body["run_id"] = normalized_id
    if request.degraded_tracking:
        body["degraded_tracking"] = True
    return body


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
    """Attach a deterministic Review surface when the draft lacks one."""
    draft = interrupt.get("draft")
    if isinstance(draft, Mapping):
        existing = draft.get("a2ui")
        if isinstance(existing, Mapping):
            return dict(interrupt)
    summary = summary_text_from_interrupt_draft(draft)
    surface = project_review_confirm(summary)
    if run_id is not None:
        surface = {**surface, "surface_id": f"{run_id}-review-confirm"}
    if isinstance(draft, Mapping):
        merged_draft = dict(draft)
        merged_draft["a2ui"] = surface
    else:
        merged_draft = {"draft": summary, "a2ui": surface}
    return {**dict(interrupt), "draft": merged_draft}


def canonicalize_run_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project fetched records through the public Review pause contract."""
    if (
        record.get("agent") != "review"
        or record.get("status") != "input_required"
    ):
        return dict(record)
    result = record.get("result")
    if not isinstance(result, Mapping):
        return dict(record)
    interrupt = result.get("interrupt")
    if not isinstance(interrupt, Mapping):
        return dict(record)
    run_id = record.get("run_id")
    return {
        **dict(record),
        "result": {
            **dict(result),
            "interrupt": _ensure_review_interrupt_surface(
                interrupt,
                run_id=run_id if isinstance(run_id, str) else None,
            ),
        },
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
                result={},
                persisted=run_id is not None,
                degraded_tracking=degraded_tracking,
                include_run_id=include_run_id,
            ),
        )
    result = body.get("result")
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
        else dict(interrupt)
    )
    validated = build_agent_run_response(
        run_id=request.run_id,
        agent=request.agent,
        status="input_required",
        task_ids=request.task_ids,
        result={"interrupt": projected_interrupt},
        persisted=request.persisted,
        degraded_tracking=request.degraded_tracking,
        include_run_id=request.include_run_id,
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
    """Lift legacy formatted-only results into the canonical projection."""
    formatted = result.get("formatted")
    if not isinstance(formatted, Mapping):
        raise LifecycleInvariantError(SafeErrorCode.PROJECTION_FAILED)
    canonical = empty_agent_result(degraded=degraded_tracking)
    merged_formatted = dict(formatted)
    for key, value in canonical["formatted"].items():
        merged_formatted.setdefault(key, value)
    merged_execution = dict(canonical["execution"])
    execution = result.get("execution")
    if isinstance(execution, Mapping):
        merged_execution.update(dict(execution))
        tracking = execution.get("tracking")
        if isinstance(tracking, Mapping):
            merged_execution["tracking"] = {
                **canonical["execution"]["tracking"],
                **dict(tracking),
            }
    if task_ids and not merged_execution.get("tasks"):
        merged_execution["tasks"] = [
            {"id": task_id, "accepted": True} for task_id in task_ids
        ]
    if not merged_execution.get("output_dirs") and isinstance(
        merged_formatted.get("output_dirs"), list
    ):
        merged_execution["output_dirs"] = list(merged_formatted["output_dirs"])
    canonical_result = dict(result)
    canonical_result["formatted"] = merged_formatted
    canonical_result["execution"] = merged_execution
    return canonical_result
