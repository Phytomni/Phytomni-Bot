# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Native agent-run dispatch helpers.

The public HTTP application keeps these helpers available through
``api.app`` for compatibility.  Dependencies are resolved from that module
at call time so the long-standing monkeypatch seams used by the HTTP tests
remain intact while the route facade stays small.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any

from fastapi import HTTPException

from ..agents.brief_gene.resolve_query import resolve_brief_gene_user_query
from ..agents.deep_genome.resolve_query import resolve_deep_genome_user_query
from ..agents.design.resolve_query import resolve_design_user_query
from ..agents.network.resolve_query import resolve_network_user_query
from ..mcp.app import invoke_tool_enveloped, validate_tool_arguments
from ..mcp.result_formatting import strip_agent_result
from ..runtime.background_submission import (
    BackgroundSubmissionLaunchError,
    BackgroundSubmissionOutcome,
    launch_background_submission,
    reserve_background_submission,
)
from ..runtime.request_context import (
    current_accepted_task_ids,
    current_recorder_degraded,
    current_request_id,
    current_request_user,
    current_run_id,
)
from ..runtime.run_registry import RunRequestInfo
from ..runtime.stage_trace import DataStage
from ..runtime.submission_outcome import (
    project_submission_warnings as _project_warnings,
)
from . import run_lifecycle
from .attachments import validate_native_attachments
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    build_agent_run_response,
    canonicalize_agent_run_body,
    empty_agent_result,
)
from .resolvers import ResolverDispatch, apply_runs_resolver

__all__ = [
    "BackgroundSubmissionLaunchError",
    "BackgroundSubmissionOutcome",
    "apply_runs_resolver",
    "invoke_tool_enveloped",
    "launch_background_submission",
    "reserve_background_submission",
    "resolve_brief_gene_user_query",
    "resolve_deep_genome_user_query",
    "resolve_design_user_query",
    "resolve_network_user_query",
    "validate_tool_arguments",
    "_AgentRunPreparation",
    "_AgentRunPreflight",
    "_background_agent_run_response",
    "_execute_background_agent_run",
    "_format_agent_run_result",
    "_invoke_agent_run",
    "_prepare_agent_run",
    "_preflight_agent_run",
    "_remote_agent_run_response",
    "_resolve_remote_run",
    "_sync_agent_run_response",
]


def _app_module() -> Any:
    """Load ``api.app`` after module import to retain compatibility seams."""
    return import_module(".app", package=__package__)


def _app_attr(name: str) -> Any:
    """Resolve one private compatibility seam without static access warnings."""
    return getattr(_app_module(), name)


@dataclass(frozen=True, slots=True)
class _AgentRunPreparation:
    """Resolved context shared by one native agent-run response."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo
    resolve_meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _AgentRunPreflight:
    """Synchronous validation and immutable request context for one run."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo


def _project_submission_warnings(raw: Any) -> list[dict[str, Any]]:
    """Project safe remote-submission warnings into HTTP execution state."""
    if not isinstance(raw, Mapping):
        return []
    return _project_warnings(raw.get("submission_warnings"))


def _request_info_query(
    arguments: Mapping[str, Any], request_json: str | None
) -> str | None:
    """Resolve the original query without trusting selected arguments."""
    value = arguments.get("user_query")
    if isinstance(value, str):
        return value
    try:
        value = json.loads(request_json or "{}").get("user_query")
    except (AttributeError, TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _preflight_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: str | None,
    request_json: str | None,
) -> _AgentRunPreflight:
    """Validate structural inputs and capture request context before dispatch."""
    app = _app_module()
    tool_name = _app_attr("_AGENT_SLUG_TO_TOOL").get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    if agent in _app_attr("_BACKGROUND_SUBMISSION_AGENT_SLUGS"):
        validation_arguments = deepcopy(arguments)
        if agent == "design" and validation_arguments.get("resolve_gene_id"):
            validation_arguments.setdefault("species_code", "ath")
            validation_arguments.setdefault("gene_id", "AT1G01010")
        elif agent == "network" and validation_arguments.get("resolve_to_id"):
            validation_arguments.setdefault("species_code", "osa")
            validation_arguments.setdefault("to_id", "TO:0000001")
        validate_tool_arguments(tool_name, validation_arguments)
    owner = current_request_user() or "anonymous"
    validate_native_attachments(
        agent,
        arguments,
        owner=owner,
        db_path=_app_attr("resolve_tasks_db_path")(),
    )
    return _AgentRunPreflight(
        tool_name=tool_name,
        owner=owner,
        request_info=RunRequestInfo(
            dialogue_id=dialogue_id,
            request_id=current_request_id(),
            query=_request_info_query(arguments, request_json),
            tool_name=tool_name,
            model=None,
            request_json=request_json,
            locale=app.current_effective_locale(),
        ),
    )


async def _prepare_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
) -> _AgentRunPreparation:
    """Resolve semantic arguments after structural preflight."""
    app = _app_module()
    resolve_meta = await app.apply_runs_resolver(
        agent,
        arguments,
        dispatch=ResolverDispatch(
            brief_gene_resolver=app.resolve_brief_gene_user_query,
            deep_genome_resolver=app.resolve_deep_genome_user_query,
            design_resolver=app.resolve_design_user_query,
            network_resolver=app.resolve_network_user_query,
        ),
    )
    return _AgentRunPreparation(
        tool_name=preflight.tool_name,
        owner=preflight.owner,
        request_info=preflight.request_info,
        resolve_meta=resolve_meta,
    )


def _format_agent_run_result(
    envelope: Any,
    *,
    resolve_meta: dict[str, Any],
    debug: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build raw and default-projected result blocks from one envelope."""
    formatted_dict = asdict(envelope.formatted)
    if resolve_meta:
        existing_meta = formatted_dict.get("metadata") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
    execution = getattr(envelope, "execution", None)
    if execution is None:
        # Keep compatibility for narrow adapters that still provide the
        # historical two-field envelope during the migration.
        execution_dict: dict[str, Any] = {
            "warnings": _project_submission_warnings(envelope.raw),
        }
    else:
        execution_dict = asdict(execution)
    result = {
        "formatted": formatted_dict,
        "execution": execution_dict,
        "raw": envelope.raw,
    }
    response_result = result if debug else strip_agent_result(result)
    return result, response_result


async def _execute_background_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
    debug: bool,
) -> BackgroundSubmissionOutcome:
    """Resolve, invoke, and project one already-reserved background run."""
    app = _app_module()
    prepared = await _app_attr("_prepare_agent_run")(
        agent=agent,
        arguments=arguments,
        preflight=preflight,
    )
    envelope = await app.invoke_tool_enveloped(prepared.tool_name, arguments)
    result, _response_result = _app_attr("_format_agent_run_result")(
        envelope,
        resolve_meta=prepared.resolve_meta,
        debug=debug,
    )
    return BackgroundSubmissionOutcome(
        accepted_task_ids=current_accepted_task_ids(),
        # The detached worker persists this projection.  Debug is a public
        # response option, never an authorization to retain raw agent output.
        result=strip_agent_result(result),
        degraded=current_recorder_degraded(),
    )


def _background_agent_run_response(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
    debug: bool,
) -> tuple[dict[str, Any], int]:
    """Reserve and launch one background run before returning 202."""
    db_path = _app_attr("resolve_tasks_db_path")()
    worker_arguments = deepcopy(arguments)
    reservation = _app_attr("reserve_background_submission")(
        agent=agent,
        owner=preflight.owner,
        request_info=preflight.request_info,
        db_path=db_path,
    )
    _app_attr("launch_background_submission")(
        reservation,
        lambda: _execute_background_agent_run(
            agent=agent,
            arguments=worker_arguments,
            preflight=preflight,
            debug=debug,
        ),
        db_path=db_path,
    )
    body = build_agent_run_response(
        run_id=reservation.run_id,
        agent=agent,
        status="running",
        task_ids=[],
        result=empty_agent_result(),
        persisted=True,
        degraded_tracking=False,
    )
    return body, 202


def _remote_agent_run_response(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    response_result: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Shape the 202 submission response and expose tracking degradation."""
    resolved = _app_attr("_resolve_remote_run")(owner)
    _app_attr("_stamp_remote_request_info")(
        run_id=resolved.run_id, owner=owner, request_info=request_info
    )
    result = (
        empty_agent_result(degraded=True)
        if resolved.degraded_tracking
        else response_result
    )
    body = build_agent_run_response(
        run_id=resolved.run_id,
        agent=agent,
        status="running",
        task_ids=resolved.task_ids,
        result=result,
        persisted=resolved.persisted,
        degraded_tracking=resolved.degraded_tracking,
    )
    return body, 202


async def _sync_agent_run_response(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    result: dict[str, Any],
    response_result: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist and shape a terminal synchronous agent response."""
    try:
        persistence_context = (
            _app_attr("trace_data_stage")(
                DataStage.RUN_PERSIST,
                dependency="run_registry",
            )
            if agent == "data"
            else nullcontext()
        )
        async with persistence_context:
            run_id = _app_attr("_record_sync_run")(
                agent=agent,
                owner=owner,
                result=result,
                request_info=request_info,
            )
    except run_lifecycle.RunPersistenceError as exc:
        raise SafeApiError(
            status_code=500,
            code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
            message="The completed run could not be persisted.",
            stage="run_persist",
            retryable=False,
        ) from exc
    canonical = canonicalize_agent_run_body(
        {
            "id": run_id,
            "object": "agent.run",
            "agent": agent,
            "status": "succeeded",
            "task_ids": [],
            "result": response_result,
        }
    )
    body = build_agent_run_response(
        run_id=run_id,
        agent=agent,
        status="succeeded",
        task_ids=(),
        result=canonical["result"],
        persisted=True,
        degraded_tracking=canonical.get("degraded_tracking") is True,
    )
    return body, 200


def _resolve_remote_run(owner: str) -> run_lifecycle.ResolvedRemoteRun:
    """Compatibility seam for remote-run context recovery."""
    return run_lifecycle.resolve_remote_run(
        owner,
        run_id=current_run_id(),
        accepted_task_ids=current_accepted_task_ids(),
        recorder_degraded=current_recorder_degraded(),
        db_path=_app_attr("resolve_tasks_db_path")(),
    )


async def _invoke_agent_run_request(
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Dispatch a normalized native run request through lifecycle stages."""
    agent = request["agent"]
    arguments = request["arguments"]
    dialogue_id = request.get("dialogue_id")
    request_json = request.get("request_json")
    debug = request.get("debug", False)
    preflight = _app_attr("_preflight_agent_run")(
        agent=agent,
        arguments=arguments,
        dialogue_id=dialogue_id,
        request_json=request_json,
    )
    if agent in _app_attr("_BACKGROUND_SUBMISSION_AGENT_SLUGS"):
        try:
            return _app_attr("_background_agent_run_response")(
                agent=agent,
                arguments=arguments,
                preflight=preflight,
                debug=debug,
            )
        except BackgroundSubmissionLaunchError as exc:
            raise SafeApiError(
                status_code=500,
                code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
                message="The background run could not be started.",
                stage="submission_start",
                retryable=False,
            ) from exc
    prepared = await _app_attr("_prepare_agent_run")(
        agent=agent,
        arguments=arguments,
        preflight=preflight,
    )
    return await _invoke_prepared_agent_run(request, prepared)


async def _invoke_prepared_agent_run(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> tuple[dict[str, Any], int]:
    """Format and settle a request after structural and semantic setup."""
    app = _app_module()
    agent = request["agent"]
    arguments = request["arguments"]
    private_agent_state = request.get("private_agent_state")
    debug = request.get("debug", False)
    context_review = (
        isinstance(private_agent_state, Mapping)
        and private_agent_state.get("review_adapter") is not None
    )
    if agent == "review" and not context_review:
        execution = await _app_attr("_run_review_with_interrupt")(
            arguments=arguments,
            request_info=prepared.request_info,
        )
        return _app_attr("_review_run_body")(execution, debug=debug), 200
    try:
        envelope = await app.invoke_tool_enveloped(
            prepared.tool_name,
            arguments,
            conversation_messages=request.get("conversation_messages", ()),
            agent_thread_id=request.get("agent_thread_id"),
            private_agent_state=private_agent_state,
        )
        format_context = (
            _app_attr("trace_data_stage")(
                DataStage.RESULT_FORMAT,
                dependency="formatter",
            )
            if agent == "data"
            else nullcontext()
        )
        async with format_context:
            result, response_result = _app_attr("_format_agent_run_result")(
                envelope,
                resolve_meta=prepared.resolve_meta,
                debug=debug,
            )
        if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
            return _app_attr("_remote_agent_run_response")(
                agent=agent,
                owner=prepared.owner,
                request_info=prepared.request_info,
                response_result=response_result,
            )
        return await _app_attr("_sync_agent_run_response")(
            agent=agent,
            owner=prepared.owner,
            request_info=prepared.request_info,
            result=result,
            response_result=response_result,
        )
    except SafeApiError:
        raise
    except Exception as exc:
        safe_error = (
            getattr(app, "_factory").project_data_stage_error(exc)
            if agent == "data"
            else None
        )
        if safe_error is None:
            raise
        raise safe_error from exc


async def _invoke_agent_run(**kwargs: Any) -> tuple[dict[str, Any], int]:
    """Dispatch one native run through the shared lifecycle contract."""
    return await _invoke_agent_run_request(kwargs)
