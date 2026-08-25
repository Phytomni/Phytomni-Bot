# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Runtime-owned resume handling for HTTP A2UI uplinks."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from ..agents.shared.a2ui import (
    A2uiActionEnvelope,
    action_to_resume_payload,
    review_action_to_resume,
    validate_a2ui_surface,
)
from ..mcp.result_formatting import strip_agent_result
from ..runtime.checkpoint_instrumentation_v2 import (
    record_projected_input_required,
)
from ..runtime.error_types import LOCAL_DURABLE_ERRORS
from ..runtime.execution_instrumentation_v2 import current_execution_boundary
from ..runtime.locale import (
    bind_effective_locale,
    resolve_effective_locale,
)
from ..runtime.resume import NoCheckpointError, detect_interrupt
from ..runtime.run_registry import (
    RunRecord,
    RunRegistry,
)
from . import run_lifecycle
from .a2ui_projection import (
    ReviewSurfaceProjectionError,
    chat_interrupt_body,
    chat_interrupt_result,
    format_chat_result,
    project_review_interrupt,
    review_interrupt_body,
    review_interrupt_result,
    submitted_a2ui_value,
)
from .a2ui_projection import (
    review_projection_error as _review_projection_error,
)
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    build_agent_run_response,
    canonicalize_agent_run_body,
)
from .schemas import A2uiActionRequest, ResumeRequest

if TYPE_CHECKING:
    from .a2ui_runtime import A2UIRuntimeDependencies

_LOGGER = logging.getLogger(__name__)
_PERSISTENCE_ERRORS = LOCAL_DURABLE_ERRORS


def _checkpoint_error() -> SafeApiError:
    """Return the stable error for a missing durable pause checkpoint."""
    return SafeApiError(
        status_code=409,
        code=SafeErrorCode.CHECKPOINT_NOT_AVAILABLE.value,
        message="This input request is no longer available.",
        stage="resume_checkpoint",
    )


@dataclass(frozen=True, slots=True)
class _GraphResumeRequest:
    """Inputs needed to invoke one Runtime-authorized graph resume."""

    run_id: str
    graph: Any
    payload: Mapping[str, Any]
    resume_graph: Any
    failure_detail: str


async def _invoke_graph(
    request: _GraphResumeRequest,
) -> dict[str, Any]:
    """Invoke a graph; the outer Runtime owns every lifecycle transition."""
    try:
        return await request.resume_graph(
            request.graph,
            request.run_id,
            request.payload,
        )
    except NoCheckpointError as exc:
        raise _checkpoint_error() from exc
    except Exception as exc:
        _LOGGER.error(
            "%s for run %s (%s)",
            request.failure_detail,
            request.run_id,
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=request.failure_detail,
        ) from exc


def open_surface_for_action(
    record: RunRecord,
    *,
    surface_id: str,
    widget: str,
) -> Mapping[str, Any]:
    """Return the open A2UI draft surface or raise an HTTP conflict."""
    if record.status not in {"input_required", "waiting_input"}:
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    stored = record.result or {}
    interrupt = stored.get("interrupt") or {}
    draft = interrupt.get("draft") or {}
    open_surface = draft.get("a2ui")
    if not isinstance(open_surface, Mapping):
        raise HTTPException(status_code=409, detail="no open a2ui surface")
    if open_surface.get("surface_id") != surface_id:
        raise HTTPException(status_code=409, detail="surface_id mismatch")
    if open_surface.get("widget") != widget:
        raise HTTPException(status_code=400, detail="widget mismatch")
    return open_surface


def _bind_record_locale(
    record: RunRecord,
    *,
    registry: RunRegistry,
    owner: str,
) -> None:
    """Bind a run's stored locale, backfilling legacy rows once."""
    locale = record.request_info.locale
    if locale is None:
        locale = resolve_effective_locale(
            explicit=None,
            accept_language=None,
            latest_user_query=record.request_info.query or "",
        )
        request_info = replace(record.request_info, locale=locale)
        try:
            persisted = registry.update_request_info(
                record.spec.run_id,
                owner=owner,
                request_info=request_info,
            )
        except _PERSISTENCE_ERRORS as exc:
            raise run_lifecycle.RunPersistenceError(
                "run locale persistence failed"
            ) from exc
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "run locale persistence failed"
            )
    bind_effective_locale(locale)


@dataclass(frozen=True, slots=True)
class _ActionContext:
    """Validated owner, surface, graph, and registry for one uplink."""

    owner: str
    agent: str
    surface: Mapping[str, Any]
    resume_payload: dict[str, Any]
    graph: Any
    registry: RunRegistry


def _prepare_action_context(
    *,
    run_id: str,
    body: A2uiActionRequest,
    dependencies: A2UIRuntimeDependencies,
) -> _ActionContext:
    """Validate ownership and translate one Web action envelope."""
    owner = dependencies.persistence.current_user() or "anonymous"
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    record = registry.get_run(run_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {run_id}",
        )
    _bind_record_locale(record, registry=registry, owner=owner)
    agent = record.spec.agent
    if agent not in ("chat", "review"):
        raise HTTPException(
            status_code=400,
            detail="unsupported agent for a2ui",
        )
    surface = open_surface_for_action(
        record,
        surface_id=body.surface_id,
        widget=body.widget,
    )
    try:
        envelope = A2uiActionEnvelope.model_validate(body.model_dump())
        if agent == "chat":
            resume_payload = action_to_resume_payload(envelope)
            graph = dependencies.graphs.chat_graph()
        else:
            resume_payload = review_action_to_resume(envelope)
            graph = dependencies.graphs.review_graph()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    boundary = current_execution_boundary()
    if (
        boundary is None
        or boundary.context.run_id != run_id
        or boundary.context.execution_id != record.request_info.execution_id
    ):
        raise HTTPException(
            status_code=409,
            detail="legacy A2UI execution is read-only",
        )
    return _ActionContext(
        owner=owner,
        agent=agent,
        surface=surface,
        resume_payload=resume_payload,
        graph=graph,
        registry=registry,
    )


def _settled_action_interrupt(
    *,
    run_id: str,
    context: _ActionContext,
    interrupt: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist and shape a re-interrupt from one resumed A2UI graph."""
    if context.agent == "chat":
        interrupt_dict = dict(interrupt)
        try:
            result = chat_interrupt_result(interrupt_dict)
            persisted = context.registry.update_active_result(
                run_id,
                owner=context.owner,
                result=result,
            )
        except _PERSISTENCE_ERRORS as exc:
            raise run_lifecycle.RunPersistenceError(
                "chat A2UI pause persistence failed"
            ) from exc
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "chat A2UI pause persistence failed"
            )
        record_projected_input_required(result)
        return (
            chat_interrupt_body(run_id=run_id, interrupt=interrupt_dict),
            200,
        )
    try:
        interrupt_dict = project_review_interrupt(interrupt)
        pause_result = review_interrupt_result(interrupt_dict)
        persisted = context.registry.update_active_result(
            run_id,
            owner=context.owner,
            result=pause_result,
        )
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "review A2UI pause persistence failed"
            )
    except ReviewSurfaceProjectionError as exc:
        raise _review_projection_error() from exc
    record_projected_input_required(pause_result)
    return (
        review_interrupt_body(thread_id=run_id, interrupt=interrupt_dict),
        200,
    )


def _terminal_action_result(
    *,
    context: _ActionContext,
    final_state: Mapping[str, Any],
    format_review_result: Any,
) -> dict[str, Any]:
    """Format a terminal Chat or Review A2UI resume result."""
    if context.agent == "chat":
        return format_chat_result(
            final_state,
            prior_surface=context.surface,
            resume_payload=context.resume_payload,
        )
    return {
        **format_review_result(final_state),
        "a2ui": submitted_a2ui_value(
            context.surface,
            context.resume_payload,
        ),
    }


def _review_reinterrupt_response(
    *,
    context: Any,
    thread_id: str,
    interrupt: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist a fresh Review surface under the active Runtime operation."""
    try:
        interrupt_dict = project_review_interrupt(interrupt)
        pause_result = review_interrupt_result(interrupt_dict)
        persisted = context.registry.update_active_result(
            thread_id,
            owner=context.owner,
            result=pause_result,
        )
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "review A2UI pause persistence failed"
            )
    except ReviewSurfaceProjectionError as exc:
        raise _review_projection_error() from exc
    record_projected_input_required(pause_result)
    return (
        review_interrupt_body(
            thread_id=thread_id,
            interrupt=interrupt_dict,
        ),
        200,
    )


async def resume_a2ui_run(
    *,
    run_id: str,
    body: A2uiActionRequest,
    debug: bool,
    dependencies: A2UIRuntimeDependencies,
) -> tuple[dict[str, Any], int]:
    """Resume a Chat or Review action inside its canonical Runtime boundary."""
    if run_id != body.run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")

    context = _prepare_action_context(
        run_id=run_id,
        body=body,
        dependencies=dependencies,
    )
    if not await dependencies.graphs.has_checkpoint(context.graph, run_id):
        raise _checkpoint_error()
    final_state = await _invoke_graph(
        _GraphResumeRequest(
            run_id=run_id,
            graph=context.graph,
            payload=context.resume_payload,
            resume_graph=dependencies.graphs.resume_graph,
            failure_detail="a2ui resume failed",
        )
    )
    interrupt_after = detect_interrupt(final_state, run_id)
    if interrupt_after is not None:
        return _settled_action_interrupt(
            run_id=run_id,
            context=context,
            interrupt=interrupt_after,
        )

    result = _terminal_action_result(
        context=context,
        final_state=final_state,
        format_review_result=dependencies.persistence.format_review_result,
    )
    response_result = result if debug else strip_agent_result(result)
    return (
        run_lifecycle.agent_run_response(
            run_id=run_id,
            agent=context.agent,
            status="succeeded",
            result=response_result,
        ),
        200,
    )


@dataclass(frozen=True, slots=True)
class ReviewExecution:
    """Internal result of one interrupt-aware ReviewAgent graph run."""

    run_id: str
    status: str
    result: dict[str, Any] | None = None
    interrupt: dict[str, Any] | None = None


def review_run_body(
    execution: ReviewExecution,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Shape a ReviewAgent execution as an ``agent.run`` response."""
    if execution.interrupt is not None:
        return review_interrupt_body(
            thread_id=execution.run_id,
            interrupt=execution.interrupt,
        )
    result = execution.result or {"formatted": {"answer": ""}, "raw": None}
    response_result = result if debug else strip_agent_result(result)
    canonical = canonicalize_agent_run_body(
        {
            "id": execution.run_id,
            "run_id": execution.run_id,
            "object": "agent.run",
            "agent": "review",
            "status": execution.status,
            "task_ids": [],
            "result": response_result,
        }
    )
    canonical_result = dict(canonical["result"])
    if debug and "raw" in result:
        canonical_result["raw"] = result["raw"]
    return build_agent_run_response(
        run_id=execution.run_id,
        agent="review",
        status=execution.status,
        task_ids=(),
        result=canonical_result,
        persisted=True,
    )


@dataclass(frozen=True, slots=True)
class _ReviewResumeContext:
    """Validated Review run ownership and prior A2UI surface."""

    owner: str
    registry: RunRegistry
    prior_surface: Mapping[str, Any] | None


def _prepare_review_resume_context(
    *,
    thread_id: str,
    dependencies: A2UIRuntimeDependencies,
) -> _ReviewResumeContext:
    """Load a paused Review run and capture its submitted-surface context."""
    owner = dependencies.persistence.current_user() or "anonymous"
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    record = registry.get_run(thread_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {thread_id}",
        )
    _bind_record_locale(record, registry=registry, owner=owner)
    if record.status not in {"input_required", "waiting_input"}:
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    stored = record.result or {}
    interrupt_stored = stored.get("interrupt") or {}
    draft = interrupt_stored.get("draft") or {}
    candidate = draft.get("a2ui")
    if not isinstance(candidate, Mapping):
        raise _checkpoint_error()
    try:
        validate_a2ui_surface(candidate)
    except (TypeError, ValueError) as exc:
        raise _checkpoint_error() from exc
    boundary = current_execution_boundary()
    if (
        boundary is None
        or boundary.context.run_id != thread_id
        or boundary.context.execution_id != record.request_info.execution_id
    ):
        raise HTTPException(
            status_code=409,
            detail="legacy A2UI execution is read-only",
        )
    return _ReviewResumeContext(
        owner=owner,
        registry=registry,
        prior_surface=dict(candidate),
    )


async def resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool,
    dependencies: A2UIRuntimeDependencies,
) -> tuple[dict[str, Any], int]:
    """Resume a ReviewAgent run through its canonical Runtime operation."""
    context = _prepare_review_resume_context(
        thread_id=thread_id,
        dependencies=dependencies,
    )
    graph = dependencies.graphs.review_graph()
    if not await dependencies.graphs.has_checkpoint(graph, thread_id):
        raise _checkpoint_error()
    prior_surface = context.prior_surface
    assert prior_surface is not None
    validate_a2ui_surface(prior_surface)
    final_state = await _invoke_graph(
        _GraphResumeRequest(
            run_id=thread_id,
            graph=graph,
            payload={"approved": payload.approved, "edits": payload.edits},
            resume_graph=dependencies.graphs.resume_graph,
            failure_detail="review resume failed",
        )
    )
    interrupt = detect_interrupt(final_state, thread_id)
    if interrupt is not None:
        return _review_reinterrupt_response(
            context=context,
            thread_id=thread_id,
            interrupt=interrupt,
        )
    result = dependencies.persistence.format_review_result(final_state)
    result = {
        **result,
        "a2ui": submitted_a2ui_value(
            prior_surface,
            {"approved": payload.approved},
        ),
    }
    execution = ReviewExecution(
        run_id=thread_id,
        status="succeeded",
        result=result,
    )
    return review_run_body(execution, debug=debug), 200


__all__ = [
    "open_surface_for_action",
    "resume_a2ui_run",
    "resume_review_run",
]
