# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable claim and resume runtime for HTTP A2UI uplinks."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from ..agents.shared.a2ui import (
    A2uiActionEnvelope,
    action_to_resume_payload,
    review_action_to_resume,
    validate_a2ui_surface,
)
from ..config.defaults import ApiConfig
from ..mcp.result_formatting import strip_agent_result
from ..runtime.resume import NoCheckpointError, detect_interrupt
from ..runtime.run_registry import (
    A2UIActionClaim,
    A2UIActionConflict,
    RunRecord,
    RunRegistry,
)
from ..storage.path_policy import IdFactory
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
from .a2ui_review_persistence import (
    review_projection_error as _review_projection_error,
)
from .a2ui_review_persistence import (
    settle_review_pause as _settle_review_pause,
)
from .a2ui_review_persistence import (
    settle_review_projection_failure as _settle_review_projection_failure,
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
_PERSISTENCE_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    sqlite3.Error,
)


def _failed_resume_result() -> dict[str, Any]:
    """Return a safe failure payload without retaining backend exceptions."""
    return {
        "formatted": {"answer": ""},
        "raw": None,
        "error": "a2ui resume failed",
    }


def _checkpoint_error() -> SafeApiError:
    """Return the stable error for a missing durable pause checkpoint."""
    return SafeApiError(
        status_code=409,
        code=SafeErrorCode.CHECKPOINT_NOT_AVAILABLE.value,
        message="This input request is no longer available.",
        stage="resume_checkpoint",
    )


def _action_conflict_error() -> SafeApiError:
    """Return the stable error for a previously-consumed input request."""
    return SafeApiError(
        status_code=409,
        code=SafeErrorCode.A2UI_ACTION_CONFLICT.value,
        message="This input request has already been handled.",
        stage="resume_claim",
    )


def _complete_claim_best_effort(
    claim: A2UIActionClaim,
    *,
    owner: str,
    registry: RunRegistry,
    outcome: str,
) -> None:
    """Record a claimed outcome without masking the original failure."""
    try:
        completed = registry.complete_a2ui_action(
            claim,
            owner=owner,
            outcome=outcome,
        )
    except _PERSISTENCE_ERRORS as exc:
        _LOGGER.error(
            "a2ui action audit completion failed (%s)",
            exc.__class__.__name__,
        )
        return
    if completed is not True:
        _LOGGER.error("a2ui action audit completion returned false")


def _complete_claim_or_raise(
    claim: A2UIActionClaim,
    *,
    owner: str,
    registry: RunRegistry,
    outcome: str,
) -> None:
    """Require durable completion for a claimed successful transition."""
    try:
        completed = registry.complete_a2ui_action(
            claim,
            owner=owner,
            outcome=outcome,
        )
    except _PERSISTENCE_ERRORS as exc:
        raise run_lifecycle.RunPersistenceError(
            "a2ui action audit completion failed"
        ) from exc
    if completed is not True:
        raise run_lifecycle.RunPersistenceError(
            "a2ui action audit completion returned false"
        )


def _settle_failed_resume(
    registry: RunRegistry,
    *,
    run_id: str,
    owner: str,
) -> None:
    """Best-effort failed settlement after a claimed resume breaks."""
    try:
        persisted = registry.settle_run(
            run_id,
            owner=owner,
            status="failed",
            result=_failed_resume_result(),
        )
    except _PERSISTENCE_ERRORS as exc:
        _LOGGER.error(
            "a2ui failed settlement raised (%s)", exc.__class__.__name__
        )
        return
    if persisted is not True:
        _LOGGER.error("a2ui failed settlement returned false")


def _settle_claim_failure(
    claim: A2UIActionClaim,
    *,
    registry: RunRegistry,
    owner: str,
    run_id: str,
) -> None:
    """Best-effort audit and run settlement after a claimed failure."""
    _complete_claim_best_effort(
        claim,
        owner=owner,
        registry=registry,
        outcome="failed",
    )
    _settle_failed_resume(registry, run_id=run_id, owner=owner)


@dataclass(frozen=True, slots=True)
class _ClaimedGraphRequest:
    """Inputs needed to invoke one already-claimed graph resume."""

    claim: A2UIActionClaim
    context: Any
    run_id: str
    graph: Any
    payload: Mapping[str, Any]
    resume_graph: Any
    failure_detail: str


async def _invoke_claimed_graph(
    request: _ClaimedGraphRequest,
) -> dict[str, Any]:
    """Invoke a claimed graph and durably settle failures."""
    try:
        return await request.resume_graph(
            request.graph,
            request.run_id,
            request.payload,
        )
    except NoCheckpointError as exc:
        _settle_claim_failure(
            request.claim,
            registry=request.context.registry,
            owner=request.context.owner,
            run_id=request.run_id,
        )
        raise _checkpoint_error() from exc
    except Exception as exc:
        _settle_claim_failure(
            request.claim,
            registry=request.context.registry,
            owner=request.context.owner,
            run_id=request.run_id,
        )
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
    if record.status != "input_required":
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


@dataclass(frozen=True, slots=True)
class _ActionContext:
    """Validated owner, surface, graph, and registry for one uplink."""

    owner: str
    agent: str
    surface: Mapping[str, Any]
    resume_payload: dict[str, Any]
    graph: Any
    registry: RunRegistry
    format_review_result: Any


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
    agent = record.spec.agent
    if agent not in ("chat", "review"):
        raise HTTPException(
            status_code=400,
            detail="unsupported agent for a2ui",
        )
    if record.status != "input_required" and registry.list_a2ui_actions(
        owner=owner, run_id=run_id
    ):
        raise A2UIActionConflict("surface has already been claimed")
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
    return _ActionContext(
        owner=owner,
        agent=agent,
        surface=surface,
        resume_payload=resume_payload,
        graph=graph,
        registry=registry,
        format_review_result=dependencies.persistence.format_review_result,
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
            persisted = context.registry.settle_run(
                run_id,
                owner=context.owner,
                status="input_required",
                result=chat_interrupt_result(interrupt_dict),
            )
        except _PERSISTENCE_ERRORS as exc:
            raise run_lifecycle.RunPersistenceError(
                "chat A2UI pause persistence failed"
            ) from exc
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "chat A2UI pause persistence failed"
            )
        return (
            chat_interrupt_body(run_id=run_id, interrupt=interrupt_dict),
            200,
        )
    try:
        interrupt_dict = project_review_interrupt(interrupt)
        pause_result = review_interrupt_result(interrupt_dict)
        _settle_review_pause(
            context.registry,
            run_id=run_id,
            owner=context.owner,
            result=pause_result,
        )
    except ReviewSurfaceProjectionError as exc:
        _settle_review_projection_failure(
            context.registry,
            run_id=run_id,
            owner=context.owner,
            existing=True,
        )
        raise _review_projection_error() from exc
    return (
        review_interrupt_body(thread_id=run_id, interrupt=interrupt_dict),
        200,
    )


def _terminal_action_result(
    *,
    context: _ActionContext,
    final_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Format a terminal Chat or Review A2UI resume result."""
    if context.agent == "chat":
        return format_chat_result(
            final_state,
            prior_surface=context.surface,
            resume_payload=context.resume_payload,
        )
    return {
        **context.format_review_result(final_state),
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
    claim: A2UIActionClaim,
) -> tuple[dict[str, Any], int]:
    """Persist a fresh Review surface and complete the prior claim."""
    try:
        interrupt_dict = project_review_interrupt(interrupt)
        pause_result = review_interrupt_result(interrupt_dict)
        _settle_review_pause(
            context.registry,
            run_id=thread_id,
            owner=context.owner,
            result=pause_result,
        )
    except ReviewSurfaceProjectionError as exc:
        _settle_review_projection_failure(
            context.registry,
            run_id=thread_id,
            owner=context.owner,
            existing=True,
        )
        raise _review_projection_error() from exc
    _complete_claim_or_raise(
        claim,
        owner=context.owner,
        registry=context.registry,
        outcome="input_required",
    )
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
    """Resume a Chat or Review A2UI action and settle its owner-scoped row."""
    if not ApiConfig().A2UI_ENABLED:
        raise HTTPException(status_code=403, detail="a2ui disabled")
    if run_id != body.run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")

    try:
        context = _prepare_action_context(
            run_id=run_id,
            body=body,
            dependencies=dependencies,
        )
    except A2UIActionConflict as exc:
        raise _action_conflict_error() from exc
    if not await dependencies.graphs.has_checkpoint(context.graph, run_id):
        raise _checkpoint_error()
    try:
        claim = context.registry.claim_a2ui_action(
            run_id=run_id,
            owner=context.owner,
            surface_id=body.surface_id,
            widget=body.widget,
            action_id=body.action_id,
            channel="a2ui",
        )
    except A2UIActionConflict as exc:
        raise _action_conflict_error() from exc

    final_state = await _invoke_claimed_graph(
        _ClaimedGraphRequest(
            claim=claim,
            context=context,
            run_id=run_id,
            graph=context.graph,
            payload=context.resume_payload,
            resume_graph=dependencies.graphs.resume_graph,
            failure_detail="a2ui resume failed",
        )
    )

    try:
        interrupt_after = detect_interrupt(final_state, run_id)
        if interrupt_after is not None:
            response = _settled_action_interrupt(
                run_id=run_id,
                context=context,
                interrupt=interrupt_after,
            )
            _complete_claim_or_raise(
                claim,
                owner=context.owner,
                registry=context.registry,
                outcome="input_required",
            )
            return response

        result = _terminal_action_result(
            context=context,
            final_state=final_state,
        )
        try:
            persisted = context.registry.settle_run(
                run_id,
                owner=context.owner,
                status="succeeded",
                result=result,
            )
        except _PERSISTENCE_ERRORS as exc:
            raise run_lifecycle.RunPersistenceError(
                "a2ui terminal result persistence failed"
            ) from exc
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "a2ui terminal result persistence failed"
            )
        _complete_claim_or_raise(
            claim,
            owner=context.owner,
            registry=context.registry,
            outcome="succeeded",
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
    except SafeApiError:
        _settle_claim_failure(
            claim,
            registry=context.registry,
            owner=context.owner,
            run_id=run_id,
        )
        raise
    except run_lifecycle.RunPersistenceError as exc:
        _settle_claim_failure(
            claim,
            registry=context.registry,
            owner=context.owner,
            run_id=run_id,
        )
        raise HTTPException(
            status_code=500,
            detail="a2ui resume failed",
        ) from exc


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
    if record.status != "input_required":
        if registry.list_a2ui_actions(owner=owner, run_id=thread_id):
            raise A2UIActionConflict("surface has already been claimed")
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
    """Resume a ReviewAgent run and preserve the dual transport contract."""
    try:
        context = _prepare_review_resume_context(
            thread_id=thread_id,
            dependencies=dependencies,
        )
    except A2UIActionConflict as exc:
        raise _action_conflict_error() from exc
    graph = dependencies.graphs.review_graph()
    if not await dependencies.graphs.has_checkpoint(graph, thread_id):
        raise _checkpoint_error()
    prior_surface = context.prior_surface
    assert prior_surface is not None
    surface = validate_a2ui_surface(prior_surface)
    try:
        claim = context.registry.claim_a2ui_action(
            run_id=thread_id,
            owner=context.owner,
            surface_id=surface.surface_id,
            widget=surface.widget,
            action_id=(
                dependencies.persistence.current_request_id()
                or IdFactory().new_id("action", "classic")
            ),
            channel="classic",
        )
    except A2UIActionConflict as exc:
        raise _action_conflict_error() from exc

    final_state = await _invoke_claimed_graph(
        _ClaimedGraphRequest(
            claim=claim,
            context=context,
            run_id=thread_id,
            graph=graph,
            payload={"approved": payload.approved, "edits": payload.edits},
            resume_graph=dependencies.graphs.resume_graph,
            failure_detail="review resume failed",
        )
    )

    try:
        interrupt = detect_interrupt(final_state, thread_id)
        if interrupt is not None:
            return _review_reinterrupt_response(
                context=context,
                thread_id=thread_id,
                interrupt=interrupt,
                claim=claim,
            )
        result = dependencies.persistence.format_review_result(final_state)
        result = {
            **result,
            "a2ui": submitted_a2ui_value(
                prior_surface,
                {"approved": payload.approved},
            ),
        }
        try:
            persisted = context.registry.settle_run(
                thread_id,
                owner=context.owner,
                status="succeeded",
                result=result,
            )
        except _PERSISTENCE_ERRORS as exc:
            raise run_lifecycle.RunPersistenceError(
                "review terminal result persistence failed"
            ) from exc
        if persisted is not True:
            raise run_lifecycle.RunPersistenceError(
                "review terminal result persistence failed"
            )
        _complete_claim_or_raise(
            claim,
            owner=context.owner,
            registry=context.registry,
            outcome="succeeded",
        )
        execution = ReviewExecution(
            run_id=thread_id,
            status="succeeded",
            result=result,
        )
        return review_run_body(execution, debug=debug), 200
    except SafeApiError:
        _settle_claim_failure(
            claim,
            registry=context.registry,
            owner=context.owner,
            run_id=thread_id,
        )
        raise
    except run_lifecycle.RunPersistenceError as exc:
        _settle_claim_failure(
            claim,
            registry=context.registry,
            owner=context.owner,
            run_id=thread_id,
        )
        raise HTTPException(
            status_code=500,
            detail="review resume failed",
        ) from exc


__all__ = [
    "open_surface_for_action",
    "resume_a2ui_run",
    "resume_review_run",
]
