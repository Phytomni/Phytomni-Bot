# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

# pylint: disable=too-many-lines
# C0302: FastAPI app factory + every route registration lives in one
# file. Splitting per-route modules makes dependency wiring opaque
# without reducing total complexity. See docs/development/lint-exemptions.md.

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from google.protobuf import json_format
from httpx import ConnectError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..agents.brief_gene.resolve_query import resolve_brief_gene_user_query
from ..agents.deep_genome.resolve_query import resolve_deep_genome_user_query
from ..agents.design.resolve_query import resolve_design_user_query
from ..agents.expert import select_agent_tool
from ..agents.network.resolve_query import resolve_network_user_query
from ..agents.shared.a2ui import (
    select_chat_a2ui_widget,
)
from ..agents.shared.gauss import aclose_gauss_pool
from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..common.logging_config import configure_logging
from ..config.defaults import ApiConfig
from ..config.settings import SensitiveConfig
from ..interop.a2a_discovery import discover_external_a2a_capabilities
from ..interop.cache import (
    DiscoveryCache,
    get_or_create_discovery_cache,
)
from ..interop.capabilities import (
    DiscoveryError,
    DiscoveryResult,
    discover_external_mcp_capabilities,
)
from ..interop.models import InteropTarget
from ..interop.registry import (
    InteropRegistry,
    InteropRegistryError,
    load_interop_registry,
)
from ..mcp.app import (
    invoke_tool_enveloped,
    invoke_tool_streamed,
    prepare_tool_stream,
)
from ..mcp.result_formatting import (
    AguiEvent,
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..mcp.stream_lifecycle import (
    EmptyStreamError,
    PrimedAguiStream,
    StreamLifecycleState,
    prime_agui_stream,
    project_stream_failures,
)
from ..runtime.memory import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryPolicyError,
    MemorySchemaError,
    MemoryStore,
    MemoryStoreError,
    MemoryWrite,
    memory_audit_context,
    memory_policy_from_config,
)
from ..runtime.request_context import (
    bind_pre_recorded_task_id,
    bind_request_id,
    bind_request_user,
    bind_run_id,
    current_recorder_degraded,
    current_request_id,
    current_request_user,
    current_run_id,
    reset_request_var,
)
from ..runtime.run_registry import (
    RunFilter,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..runtime.task_reconcile import reconcile_task_log
from ..storage.path_policy import IdFactory
from ..version import __version__
from . import a2ui_runtime, run_lifecycle
from .a2a import runtime as a2a_runtime
from .a2a.card import build_agent_card
from .a2a.executor import (
    A2AHandlerOptions,
    A2ARegistration,
    A2ARequestHandler,
)
from .a2ui_limits import (
    A2uiPayloadError,
    A2uiPayloadTooLargeError,
    ensure_a2ui_response_size,
    read_a2ui_action_request,
)
from .admin_auth import is_service_token_valid, require_service_principal
from .agent_capabilities import serialize_agent_capability
from .auth import (
    ApiPrincipal,
    get_key_store,
    require_principal,
    scopes_satisfy,
)
from .file_upload import handle_file_upload
from .openai_mapping import (
    MODEL_TO_TOOL,
    flatten_messages,
    to_chat_completion,
    to_chat_completion_chunks,
    tool_accepts_obs,
    tool_accepts_stream,
    tool_for_model,
)
from .ratelimit import make_rate_limiter
from .relay import RelayAuditQuery, create_relay_router, get_audit_store
from .relay.audit_filter import redact_body_text
from .resolvers import (
    ResolverDispatch,
    apply_runs_resolver,
    resolve_chat_query,
)
from .schemas import (
    A2uiActionRequest,
    AgentRunRequest,
    ApiErrorDetail,
    ApiErrorResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDeleteResponse,
    ApiKeyListResponse,
    ApiKeyRecordResponse,
    ChatCompletionRequest,
    ExpertQueryRequest,
    FileUploadResponse,
    MemoryAuditListResponse,
    MemoryAuditRecordResponse,
    MemoryCreateRequest,
    MemoryDeleteResponse,
    MemoryExportResponse,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdateRequest,
    ResumeRequest,
    UploadPurpose,
)
from .stream_answer import (
    StreamAnswerAccumulator,
    resolve_stream_answer_max_bytes,
)

__all__ = ["create_app"]

_ERROR_TYPES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "unprocessable_entity",
    428: "precondition_required",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}

# Public agent slug recorded on the ``runs`` row for sync chat calls.
# Kept here (not in ``openai_mapping``) so this run-registry concern
# stays inside the API layer and does not perturb the pure mapping
# module that other API touch points already share.
_MODEL_TO_AGENT_SLUG = {
    "phyto-chat": "chat",
    "phyto-knowledge": "knowledge",
    "phyto-review": "review",
    "phyto-brief-gene": "brief_gene",
}

# Full ``slug -> MCP tool name`` map for the native ``/v1/agents``
# endpoints. Slugs mirror the ``agents/<domain>/`` directory naming
# so the run table speaks the same vocabulary as the in-process agent
# packages.
_AGENT_SLUG_TO_TOOL = {
    "chat": "ChatAgent",
    "knowledge": "KnowledgeAgent",
    "data": "DataAgent",
    "review": "ReviewAgent",
    "brief_gene": "BriefGeneAgent",
    "analyst": "AnalystAgent",
    "deep_genome": "DeepGenomeAgent",
    "research": "InSilicoResearchAgent",
    "design": "DigitalDesignAgent",
    "network": "GeneNetworkAgent",
}

# Inverse of ``_AGENT_SLUG_TO_TOOL``: the Expert router returns the MCP
# tool name the LLM selected, which this maps back to the public agent
# slug ``_invoke_agent_run`` dispatches on. Derived from the forward map
# so the two cannot drift.
_TOOL_TO_AGENT_SLUG = {
    tool: slug for slug, tool in _AGENT_SLUG_TO_TOOL.items()
}

# Slugs whose handlers submit a remote analysis task and rely on the
# ``records_submission`` chokepoint in ``runtime.submit_recorder`` to
# write the runs row with ``origin="remote"`` and bind the freshly-minted
# run id to ``current_run_id()`` so the API layer can recover it directly.
_REMOTE_AGENT_SLUGS = frozenset(
    {"analyst", "deep_genome", "research", "design", "network"}
)

# Historical Web ``tool_name`` aliases preserved on ``/v1/agents`` rows
# as ``legacy_aliases`` metadata. The route itself never accepts these
# as routing slugs; chat-ai and Phytomni-Web Go consume the list to
# build their own alias→slug translation table without out-of-band
# negotiation. Bot-added agents (brief_gene / design / network) ship
# an empty list so the shape stays uniform and a future agent must
# make an explicit declaration rather than silently inherit ``[]``.
_LEGACY_ALIASES: dict[str, list[str]] = {
    "ChatAgent": ["ChatAgent"],
    "KnowledgeAgent": ["KnowledgeAgent", "KnowledgeAgents"],
    "DataAgent": ["DataAgent", "DatabaseAgents"],
    "ReviewAgent": ["ReviewAgent", "ReviewAgents"],
    "BriefGeneAgent": [],
    "AnalystAgent": ["AnalystAgent", "AnalysisAgents"],
    "DeepGenomeAgent": ["DeepGenomeAgent"],
    "InSilicoResearchAgent": ["InSilicoResearchAgent"],
    "DigitalDesignAgent": [],
    "GeneNetworkAgent": [],
}


_LOGGER = logging.getLogger(__name__)


def _purge_expired_runs_best_effort() -> None:
    """Compatibility seam for the shared run-registry TTL purge."""
    run_lifecycle.purge_expired_runs_best_effort(
        db_path=resolve_tasks_db_path()
    )


async def _purge_expired_runs_best_effort_async() -> None:
    """Compatibility seam for the off-loop coalesced TTL purge."""
    await run_lifecycle.purge_expired_runs_best_effort_async(
        purge=_purge_expired_runs_best_effort
    )


def _claim_run_gc() -> bool:
    """Compatibility seam for the process-local GC slot."""
    return run_lifecycle.claim_run_gc()


def _release_run_gc() -> None:
    """Compatibility seam for releasing the process-local GC slot."""
    run_lifecycle.release_run_gc()


async def _schedule_run_gc(background: BackgroundTasks) -> None:
    """Compatibility seam for the FastAPI background GC dependency."""
    await run_lifecycle.schedule_run_gc(
        background, task=_purge_expired_runs_best_effort_async
    )


def _extract_answer(result: Any) -> str | None:
    """Compatibility seam for answer extraction from a stored result."""
    return run_lifecycle.extract_answer(result)


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    """Compatibility seam for flattening a registry record."""
    return run_lifecycle.run_record_to_dict(record)


def _relay_audit_record_to_dict(
    record: Any, config: ApiConfig
) -> dict[str, Any]:
    """Project a relay audit row with defense-in-depth body redaction."""
    payload = record.model_dump()
    payload["request_body"] = redact_body_text(
        payload.get("request_body"), config.RELAY_REQUEST_AUDIT_MAX_BYTES
    )
    payload["response_body"] = redact_body_text(
        payload.get("response_body"), config.RELAY_RESPONSE_AUDIT_MAX_BYTES
    )
    return payload


def _chat_a2ui_stream_app() -> Any:
    """Compatibility seam for the cached Chat A2UI graph."""
    return a2ui_runtime.build_chat_stream_app()


def _chat_a2ui_initial_state(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility seam for Chat A2UI initial-state construction."""
    return a2ui_runtime.build_chat_initial_state(arguments)


def _chat_a2ui_interrupt_result(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for paused Chat A2UI result projection."""
    return a2ui_runtime.chat_interrupt_result(interrupt)


def _submitted_a2ui_value(
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for submitted A2UI result projection."""
    return a2ui_runtime.submitted_a2ui_value(prior_surface, resume_payload)


def _format_chat_a2ui_result(
    final_state: Mapping[str, Any],
    *,
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for terminal Chat A2UI result formatting."""
    return a2ui_runtime.format_chat_result(
        final_state,
        prior_surface=prior_surface,
        resume_payload=resume_payload,
    )


def _a2ui_interrupt_body(
    *,
    run_id: str,
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the paused Chat A2UI HTTP body."""
    return a2ui_runtime.chat_interrupt_body(
        run_id=run_id,
        interrupt=interrupt,
    )


async def _resume_paused_run(
    app: Any,
    thread_id: str,
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the shared paused-graph resume kernel."""
    return await a2ui_runtime.resume_paused_graph(
        app,
        thread_id,
        resume_payload,
    )


def _open_a2ui_surface_for_action(
    record: RunRecord,
    *,
    surface_id: str,
    widget: str,
) -> Mapping[str, Any]:
    """Compatibility seam for open-surface validation."""
    return a2ui_runtime.open_surface_for_action(
        record,
        surface_id=surface_id,
        widget=widget,
    )


async def _resume_a2ui_run(
    *,
    run_id: str,
    body: A2uiActionRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Compatibility seam for the Web A2UI action resume runtime."""
    return await a2ui_runtime.resume_a2ui_run(
        run_id=run_id,
        body=body,
        debug=debug,
        dependencies=_a2ui_runtime_dependencies(),
    )


def _stream_setup_error(exc: Exception, *, priming: bool) -> HTTPException:
    """Map stream setup/prime failures to fixed pre-header HTTP errors."""
    if isinstance(exc, HTTPException):
        return exc
    status_code = 500
    detail = "stream setup failed"
    if isinstance(exc, ConnectError):
        status_code = 502
        detail = "stream upstream unavailable"
    elif isinstance(exc, TimeoutException):
        status_code = 504
        detail = "stream upstream timed out"
    elif not priming and isinstance(exc, NotImplementedError):
        status_code = 400
        detail = "streaming is not supported for this model"
    elif (
        not priming
        and isinstance(exc, McpError)
        and exc.error.code == (INVALID_PARAMS)
    ):
        status_code = 400
        detail = "invalid streaming request"
    elif isinstance(exc, EmptyStreamError):
        detail = "stream produced no data"
    return HTTPException(status_code=status_code, detail=detail)


def _failed_stream_result() -> dict[str, Any]:
    """Return the minimal failed result persisted after pre-open failure."""
    return {
        "formatted": {"answer": ""},
        "raw": None,
        "stream": True,
        "partial": True,
    }


async def _replay_primed_stream(
    primed: PrimedAguiStream,
) -> AsyncIterator[AguiEvent]:
    """Replay a primed first event before consuming its raw remainder."""
    yield primed.first
    async for event in primed.remainder:
        yield event


async def _project_primed_stream(
    primed: PrimedAguiStream,
    *,
    run_id: str,
    lifecycle_state: StreamLifecycleState | None = None,
) -> AsyncIterator[AguiEvent]:
    """Project a primed raw stream through one typed lifecycle state."""
    state = (
        lifecycle_state
        if lifecycle_state is not None
        else StreamLifecycleState()
    )
    async for event in project_stream_failures(
        _replay_primed_stream(primed),
        state=state,
        run_id=run_id,
        request_id=current_request_id() or "unknown",
    ):
        yield event


def _a2ui_runtime_dependencies() -> a2ui_runtime.A2UIRuntimeDependencies:
    """Bind app compatibility seams into the A2UI runtime record."""
    return a2ui_runtime.A2UIRuntimeDependencies(
        graphs=a2ui_runtime.A2UIGraphDependencies(
            chat_graph=_chat_a2ui_stream_app,
            chat_initial_state=_chat_a2ui_initial_state,
            review_graph=_review_stream_app,
            review_initial_state=_review_initial_state,
            validate_review=_validate_review_arguments,
            resume_graph=_resume_paused_run,
        ),
        persistence=a2ui_runtime.A2UIPersistenceDependencies(
            registry_factory=RunRegistry,
            current_user=current_request_user,
            tasks_db_path=resolve_tasks_db_path,
            create_stream_run=_create_running_stream_run,
            settle_stream_run=_settle_stream_run,
            format_review_result=_format_review_result,
        ),
        stream=a2ui_runtime.A2UIStreamDependencies(
            stream_setup_error=_stream_setup_error,
            failed_stream_result=_failed_stream_result,
            project_stream=_project_primed_stream,
        ),
    )


def _settle_a2ui_stream_failure(
    run_id: str,
    owner: str,
    settled_terminal: list[bool],
) -> None:
    """Compatibility seam for failed A2UI stream settlement."""
    a2ui_runtime.settle_a2ui_stream_failure(
        run_id,
        owner,
        settled_terminal,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _stream_chat_a2ui_confirm(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Compatibility seam for the Chat A2UI stream runtime."""
    return await a2ui_runtime.stream_chat_a2ui_confirm(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Compatibility seam for the Review A2UI stream runtime."""
    return await a2ui_runtime.stream_review_a2ui_pause(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _stream_chat_completion(  # pylint: disable=too-many-locals
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Prepare, prime, and wrap one streamed tool response.

    The wrapper is an async generator: each emitted SSE line forwards
    immediately to the client (no buffering). The run row is written
    twice: a ``running`` row before the first frame so ``RunStarted``
    carries a real, persisted registry id, then a terminal
    ``succeeded``/``failed`` settle from the ``finally`` block keyed
    on the shared typed lifecycle state (finish observed and no error). A
    client that disconnects right after ``RunFinished`` still settles
    succeeded — the answer was produced regardless of whether the
    socket stayed open to see it. Every ordinary streamed agent persists
    its accumulated answer under a soft byte cap; A2UI pause paths settle
    their structured interrupt result separately.

    Auth, rate-limit, request-id, and OBS argument prep all happen
    before this helper is called, mirroring the non-stream branch.
    """
    if (
        tool_name == "ChatAgent"
        and ApiConfig().A2UI_ENABLED
        and select_chat_a2ui_widget(user_query) is not None
    ):
        return await _stream_chat_a2ui_confirm(
            arguments=arguments,
            payload=payload,
            user_query=user_query,
        )
    agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
    owner = current_request_user() or "anonymous"
    run_id = IdFactory().new_id("run", agent_slug or "chat")
    try:
        raw_events = prepare_tool_stream(
            tool_name,
            arguments,
            run_id=run_id,
            dialogue_id=payload.dialogue_id,
        )
    except Exception as exc:
        raise _stream_setup_error(exc, priming=False) from exc

    request_info = RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name=tool_name,
        model=payload.model,
        request_json=payload.model_dump_json(),
    )
    # Stage 1: pre-mint + write running so RunStarted carries the real
    # registry id instead of an unpersisted placeholder.
    if agent_slug is not None:
        _create_running_stream_run(run_id, agent_slug, owner, request_info)
    try:
        primed = await prime_agui_stream(raw_events)
    except Exception as exc:
        if agent_slug is not None:
            _settle_stream_run(
                run_id,
                owner,
                "failed",
                _failed_stream_result(),
            )
        raise _stream_setup_error(exc, priming=True) from exc

    lifecycle_state = StreamLifecycleState()
    events = _project_primed_stream(
        primed,
        run_id=run_id,
        lifecycle_state=lifecycle_state,
    )
    accumulator = StreamAnswerAccumulator(
        events,
        max_bytes=_stream_answer_max_bytes(),
        lifecycle_state=lifecycle_state,
    )
    events = accumulator
    sse_lines = to_chat_completion_chunks(events, payload.model)

    async def _wrapped() -> AsyncIterator[str]:
        """Forward each SSE line, then settle the run from ``finally``."""
        try:
            async for line in sse_lines:
                yield line
        finally:
            # Stage 2: settle terminal keyed on reaching RunFinished,
            # not on connection close.
            if agent_slug is not None:
                status = (
                    "succeeded"
                    if lifecycle_state.reached_finish
                    and not lifecycle_state.saw_error
                    else "failed"
                )
                snap = accumulator.snapshot
                result: dict[str, Any] = {
                    "formatted": {"answer": snap.answer},
                    "raw": None,
                    "stream": True,
                    "truncated": snap.truncated,
                    "partial": status == "failed",
                }
                _settle_stream_run(run_id, owner, status, result)

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


# pylint: disable=too-many-locals
# Request validation -> DB lookup -> reconciliation -> response
# assembly inline; helpers would require 5+ context args each.
# See docs/development/lint-exemptions.md.
async def _invoke_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: str | None = None,
    request_json: str | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Dispatch one ``/v1/agents/{agent}/runs`` call and shape the body.

    Owns the slug -> tool lookup, the shared ``invoke_tool_formatted``
    call, and the origin-aware run id resolution. Returns the
    ``agent.run`` envelope: sync agents get ``status="succeeded"`` at
    HTTP 200, remote agents get ``status="running"`` plus the child
    ``task_ids`` at HTTP 202 (the submission ack convention) so a
    client can immediately poll ``/v1/runs/{id}`` for the live status.
    The formatted result is surfaced in both cases — sync clients
    consume it directly; remote clients can read the raw payload for
    additional context but should track the run by ``id`` and
    ``task_ids`` since those are uniformly populated for every
    remote agent. Analysis dedup hits also return the caller's own
    run id and fresh task id at 202 (the reuse mints a caller-owned
    row recorded through the normal chokepoint path); the only case
    where ``id=null`` / ``task_ids=[]`` is returned is when the
    local registry write fails and ``degraded_tracking: True`` is
    added to the body.

    Args:
        agent: Public agent alias (e.g. ``"chat"``).
        arguments: Tool-specific kwargs forwarded to the agent.
        debug: When True, include the raw handler payload in the
            result block. Default strips it to reduce response volume.

    Returns:
        ``(body, status_code)`` — ``body`` is the ``agent.run``
        envelope (``id`` / ``object`` / ``agent`` / ``status`` /
        ``task_ids`` / ``result``), ``status_code`` is 202 for remote
        submissions and 200 for synchronous completions.

    Raises:
        HTTPException: 404 when the slug is unknown.
    """
    tool_name = _AGENT_SLUG_TO_TOOL.get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    resolve_meta = await apply_runs_resolver(
        agent,
        arguments,
        dispatch=ResolverDispatch(
            brief_gene_resolver=resolve_brief_gene_user_query,
            deep_genome_resolver=resolve_deep_genome_user_query,
            design_resolver=resolve_design_user_query,
            network_resolver=resolve_network_user_query,
        ),
    )
    owner = current_request_user() or "anonymous"
    request_info = RunRequestInfo(
        dialogue_id=dialogue_id,
        query=(
            arguments.get("user_query")
            if isinstance(arguments.get("user_query"), str)
            else None
        ),
        tool_name=tool_name,
        model=None,
        request_json=request_json,
    )
    if agent == "review":
        execution = await _run_review_with_interrupt(
            arguments=arguments,
            request_info=request_info,
        )
        return _review_run_body(execution, debug=debug), 200
    envelope = await invoke_tool_enveloped(tool_name, arguments)
    formatted_dict = asdict(envelope.formatted)
    if resolve_meta:
        existing_meta = formatted_dict.get("metadata") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
    result = {"formatted": formatted_dict, "raw": envelope.raw}
    response_result = result if debug else strip_agent_result(result)
    if agent in _REMOTE_AGENT_SLUGS:
        run_id, task_ids = _resolve_remote_run(owner)
        _stamp_remote_request_info(
            run_id=run_id, owner=owner, request_info=request_info
        )
        body: dict[str, Any] = {
            "id": run_id,
            "object": "agent.run",
            "agent": agent,
            "status": "running",
            "task_ids": task_ids,
            "result": response_result,
        }
        # Surface the silent-failure case: the submit chokepoint hit
        # an ``sqlite3.Error`` / ``OSError`` during the local registry
        # write, so the remote tasks are live (the network call
        # already succeeded) but the local ``runs`` / ``tasks`` rows
        # were not persisted and ``GET /v1/runs/{run_id}`` will 404
        # until a manual reconcile is run. This degraded-tracking flag
        # is what makes that shape legible: a recorder failure is now
        # the ONLY path that emits ``id=None`` / ``task_ids=[]``, since
        # an analyst dedup hit flows through the normal recorder and
        # returns the caller's own run id and fresh task id.
        if current_recorder_degraded():
            body["degraded_tracking"] = True
        return body, 202
    run_id = _record_sync_run(
        agent=agent,
        owner=owner,
        result=result,
        request_info=request_info,
    )
    body = run_lifecycle.agent_run_response(
        run_id=run_id,
        agent=agent,
        status="succeeded",
        result=response_result,
        include_run_id=False,
    )
    return body, 200


def _resolve_remote_run(owner: str) -> tuple[str | None, list[str]]:
    """Compatibility seam for remote-run context recovery."""
    return run_lifecycle.resolve_remote_run(
        owner,
        run_id=current_run_id(),
        db_path=resolve_tasks_db_path(),
    )


async def _route_expert_query(
    payload: ExpertQueryRequest, *, debug: bool
) -> tuple[dict[str, Any], int]:
    """Autonomously route an Expert query and shape its agent.run body.

    Runs the in-process LLM tool selector, maps the chosen tool name back
    to its agent slug, injects ``obs_file_list`` only for obs-capable
    tools, then delegates to ``_invoke_agent_run`` so the resolved slug,
    formatted envelope, and sync(200)/remote(202) branching all come from
    the same path as ``POST /v1/agents/{slug}/runs``. When the router
    selects no tool the query falls back to the chat agent.

    Args:
        payload: The validated Expert routing request.
        debug: Whether to keep the raw handler payload in the result.

    Returns:
        ``(body, status_code)`` — the ``agent.run`` envelope plus its HTTP
        status, identical in shape to ``_invoke_agent_run``.

    Raises:
        HTTPException: 400 when ``forced_tool`` is set (unsupported in v1)
            or the router produced arguments that fail the agent schema;
            502 when the router selects a tool outside the agent set.
    """
    if payload.forced_tool is not None:
        raise HTTPException(
            status_code=400,
            detail="forced_tool is not supported in v1",
        )
    selection = await select_agent_tool(payload.user_query, payload.history)
    request_json = payload.model_dump_json()
    if selection is None:
        return await _invoke_agent_run(
            agent="chat",
            arguments={
                "user_query": payload.user_query,
                "obs_file_list": list(payload.obs_file_list),
            },
            dialogue_id=payload.dialogue_id,
            request_json=request_json,
            debug=debug,
        )
    slug = _TOOL_TO_AGENT_SLUG.get(selection.tool_name)
    if slug is None:
        _LOGGER.warning(
            "Expert router selected an unknown tool: %s",
            selection.tool_name,
        )
        raise HTTPException(
            status_code=502,
            detail="router selected an unavailable tool",
        )
    arguments = dict(selection.arguments)
    if tool_accepts_obs(selection.tool_name):
        arguments["obs_file_list"] = list(payload.obs_file_list)
    try:
        return await _invoke_agent_run(
            agent=slug,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            request_json=request_json,
            debug=debug,
        )
    except McpError as exc:
        if exc.error.code == INVALID_PARAMS:
            raise HTTPException(
                status_code=400,
                detail=f"router produced invalid arguments for {slug}",
            ) from exc
        raise


def _strip_run_result(record: dict[str, Any]) -> dict[str, Any]:
    """Strip raw from one run record's result for default-mode listing."""
    result = record.get("result")
    if isinstance(result, dict):
        return {
            **record,
            "result": strip_agent_result(result),
        }
    return record


# pylint: enable=too-many-locals


# pylint: disable=too-many-arguments
# RunFilter is built from each query arg; folding into a Pydantic
# query model triples the route boilerplate. See
# docs/development/lint-exemptions.md.
def _list_owner_runs(
    *,
    owner: str,
    status: str | None,
    agent: str | None,
    origin: str | None,
    dialogue_id: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
    offset: int,
    debug: bool = False,
) -> dict[str, Any]:
    """Compatibility seam for owner-scoped run listing."""
    return run_lifecycle.list_owner_runs(
        owner,
        run_lifecycle.RunListQuery(
            run_filter=RunFilter(
                status=status,
                agent=agent,
                origin=origin,
                dialogue_id=dialogue_id,
                created_after=created_after,
                created_before=created_before,
            ),
            limit=limit,
            offset=offset,
        ),
        debug=debug,
        context=run_lifecycle.RunLifecycleContext(
            db_path=resolve_tasks_db_path(),
            purge=_purge_expired_runs_best_effort,
            project=_project_public_run_record,
        ),
    )


# pylint: enable=too-many-arguments


_ReviewExecution = a2ui_runtime.ReviewExecution


def _review_stream_app() -> Any:
    """Compatibility seam for the ReviewAgent compiled graph."""
    return a2ui_runtime.build_review_stream_app()


def _review_initial_state(args: ReviewAgentArgs) -> Mapping[str, Any]:
    """Compatibility seam for ReviewAgent initial-state construction."""
    return a2ui_runtime.build_review_initial_state(args)


def _validate_review_arguments(arguments: dict[str, Any]) -> ReviewAgentArgs:
    """Compatibility seam for ReviewAgent argument validation."""
    return a2ui_runtime.validate_review_arguments(arguments)


def _review_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility seam for paused Review result projection."""
    return a2ui_runtime.review_interrupt_result(interrupt)


def _maybe_project_review_interrupt(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for Review interrupt projection."""
    return a2ui_runtime.project_review_interrupt(interrupt)


def _review_interrupt_body(
    *,
    thread_id: str,
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the paused Review HTTP body."""
    return a2ui_runtime.review_interrupt_body(
        thread_id=thread_id,
        interrupt=interrupt,
    )


def _format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility seam for terminal Review result formatting."""
    return a2ui_runtime.format_review_result(
        final_state,
        arguments=arguments,
    )


def _review_run_body(
    execution: _ReviewExecution,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Compatibility seam for Review run response shaping."""
    return a2ui_runtime.review_run_body(execution, debug=debug)


async def _run_review_with_interrupt(
    *,
    arguments: dict[str, Any],
    request_info: RunRequestInfo,
) -> _ReviewExecution:
    """Compatibility seam for interrupt-aware Review execution."""
    return await a2ui_runtime.run_review_with_interrupt(
        arguments=arguments,
        request_info=request_info,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Compatibility seam for Review pause/resume execution."""
    return await a2ui_runtime.resume_review_run(
        thread_id=thread_id,
        payload=payload,
        debug=debug,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _review_chat_completion_response(
    *,
    payload: ChatCompletionRequest,
    arguments: Mapping[str, object],
    user_query: str,
) -> JSONResponse:
    """Return the ReviewAgent non-stream chat response or interrupt body."""
    execution = await _run_review_with_interrupt(
        arguments=dict(arguments),
        request_info=a2ui_runtime.build_review_request_info(
            payload, user_query
        ),
    )
    if execution.interrupt is not None:
        # A ReviewAgent pause is not an OpenAI chat completion; return
        # the native interrupt body so clients can resume with the Bot
        # run id without guessing inside choices[].
        return JSONResponse(_review_run_body(execution, debug=True))
    result = execution.result or {"formatted": {"answer": ""}, "raw": None}
    completion = to_chat_completion(
        result.get("formatted", {}),
        result.get("raw"),
        payload.model,
    )
    completion["run_id"] = execution.run_id
    if not resolve_debug(payload.debug):
        completion = strip_chat_completion(completion)
    return JSONResponse(completion)


async def _stream_chat_response(
    *,
    tool_name: str,
    arguments: dict[str, object],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Validate and build a streaming chat response."""
    if tool_name == "ReviewAgent":
        if not ApiConfig().A2UI_ENABLED:
            raise HTTPException(
                status_code=400,
                detail=(
                    "streaming is not supported for human-in-the-loop "
                    "review; use stream=false and POST /v1/runs/{id}/resume"
                ),
            )
        return await _stream_review_a2ui_pause(
            arguments=dict(arguments),
            payload=payload,
            user_query=user_query,
        )
    if not tool_accepts_stream(tool_name):
        raise HTTPException(
            status_code=400,
            detail=f"streaming is not supported for model {payload.model}",
        )
    return await _stream_chat_completion(
        tool_name=tool_name,
        arguments=arguments,
        payload=payload,
        user_query=user_query,
    )


def _project_deep_genome_run(
    record: RunRecord, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for DeepGenome public projection."""
    return run_lifecycle.project_deep_genome_run(
        record,
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


def _project_public_run_record(
    record: RunRecord, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for the public run projection."""
    return run_lifecycle.project_public_run_record(
        record,
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


async def _fetch_owner_run(
    run_id: str, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for owner-checked run lookup and reconciliation."""
    return await run_lifecycle.fetch_owner_run(
        run_id,
        owner=current_request_user() or "anonymous",
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


def _record_sync_run(
    *,
    agent: str,
    owner: str,
    result: dict[str, Any],
    request_info: RunRequestInfo | None = None,
) -> str | None:
    """Compatibility seam for terminal synchronous run creation."""
    return run_lifecycle.record_sync_run(
        agent=agent,
        owner=owner,
        result=result,
        request_info=request_info,
        db_path=resolve_tasks_db_path(),
    )


def _a2a_runtime_dependencies() -> a2a_runtime.A2ARuntimeDependencies:
    """Bind app compatibility seams into the A2A runtime record."""
    registry = a2a_runtime.A2ARegistryDependencies(
        registry_factory=RunRegistry,
        current_user=current_request_user,
        tasks_db_path=resolve_tasks_db_path,
    )
    return a2a_runtime.A2ARuntimeDependencies(
        registry=registry,
        resume=a2a_runtime.A2AResumeDependencies(
            registry=registry,
            graphs=a2a_runtime.A2AGraphDependencies(
                chat_graph=_chat_a2ui_stream_app,
                review_graph=_review_stream_app,
                resume_graph=_resume_paused_run,
            ),
            projection=a2a_runtime.A2AProjectionDependencies(
                resume_payload=_a2a_resume_payload,
                interrupts=a2a_runtime.A2AInterruptDependencies(
                    chat_interrupt_result=_chat_a2ui_interrupt_result,
                    review_interrupt_result=_review_interrupt_result,
                    project_review_interrupt=_maybe_project_review_interrupt,
                    chat_interrupt_body=_a2ui_interrupt_body,
                    review_interrupt_body=_review_interrupt_body,
                ),
                format_chat_result=_format_chat_a2ui_result,
                format_review_result=_format_review_result,
            ),
        ),
    )


def _record_a2a_registration(registration: A2ARegistration) -> None:
    """Compatibility seam for A2A registration persistence."""
    a2a_runtime.record_registration(
        registration,
        dependencies=_a2a_runtime_dependencies().registry,
    )


def _get_a2a_task(task_id: str, history_length: int) -> Any:
    """Return an owner-scoped A2A task projection, or ``None``."""
    return a2a_runtime.get_task(
        task_id,
        history_length,
        dependencies=_a2a_runtime_dependencies().registry,
    )


def _a2a_resume_payload(
    agent: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate A2A input data into the shared resume payload."""
    return a2a_runtime.resume_payload(agent, arguments)


async def _resume_a2a_task(
    task_id: str,
    context_id: str,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], int] | None:
    """Compatibility seam for owner-scoped A2A pause resumption."""
    return await a2a_runtime.resume_task(
        task_id,
        context_id,
        arguments,
        dependencies=_a2a_runtime_dependencies().resume,
    )


def _create_running_stream_run(
    run_id: str, agent: str, owner: str, request_info: RunRequestInfo
) -> None:
    """Compatibility seam for initial streaming run creation."""
    run_lifecycle.create_running_stream_run(
        run_id,
        agent,
        owner,
        request_info,
        db_path=resolve_tasks_db_path(),
    )


def _stream_answer_max_bytes() -> int:
    """Return the resolved soft cap for streamed chat answer storage."""
    return resolve_stream_answer_max_bytes(ApiConfig().STREAM_ANSWER_MAX_BYTES)


def _settle_stream_run(
    run_id: str,
    owner: str,
    status: str,
    result: dict[str, Any],
) -> None:
    """Compatibility seam for streaming run settlement."""
    run_lifecycle.settle_stream_run(
        run_id,
        owner,
        status,
        result,
        context=run_lifecycle.RunLifecycleContext(
            db_path=resolve_tasks_db_path(),
            purge=_purge_expired_runs_best_effort,
        ),
    )


def _stamp_remote_request_info(
    *,
    run_id: str | None,
    owner: str,
    request_info: RunRequestInfo,
) -> None:
    """Compatibility seam for remote request metadata stamping."""
    run_lifecycle.stamp_remote_request_info(
        run_id=run_id,
        owner=owner,
        request_info=request_info,
        db_path=resolve_tasks_db_path(),
    )


def _error_response(
    status_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build a unified error-envelope JSON response.

    Args:
        status_code: HTTP status code mirrored into the body.
        message: Human-readable explanation.
        headers: Optional response headers to propagate (e.g.
            Retry-After, WWW-Authenticate) from the raised exception.

    Returns:
        JSON response carrying the unified error envelope.
    """
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            type=_ERROR_TYPES.get(status_code, "error"),
            code=status_code,
            message=message,
            request_id=current_request_id(),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers=dict(headers) if headers else None,
    )


def _memory_response(record: Any) -> MemoryResponse:
    """Convert a domain memory record into the public response shape."""
    return MemoryResponse.model_validate(record.model_dump())


def _memory_audit_response(record: Any) -> MemoryAuditRecordResponse:
    """Convert one digest-only audit record into its public shape."""
    return MemoryAuditRecordResponse.model_validate(record.model_dump())


def _memory_write(owner: str, payload: Any) -> MemoryWrite:
    """Build a domain write while keeping the owner outside the body."""
    try:
        return MemoryWrite(
            user_id=owner,
            kind=payload.kind,
            content=payload.content,
            tags=payload.tags,
            expires_at=payload.expires_at,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="memory payload failed domain validation"
        ) from exc


def _memory_revision(value: str | None, *, required: bool) -> int | None:
    """Parse the integer revision carried by an ``If-Match`` header."""
    if value is None or not value.strip():
        if required:
            raise HTTPException(
                status_code=428,
                detail="If-Match is required for memory updates",
            )
        return None
    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1].strip()
    try:
        revision = int(candidate)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="If-Match must contain a positive integer revision",
        ) from exc
    if revision < 1:
        raise HTTPException(
            status_code=400,
            detail="If-Match must contain a positive integer revision",
        )
    return revision


def request_context_middleware(app: ASGIApp) -> ASGIApp:
    """Wrap an ASGI app to bind a per-request correlation id.

    A generated request id is bound to the contextvar for the request's
    lifetime and echoed as the ``X-Request-Id`` response header so the
    error envelope and clients can correlate a call. The user contextvar
    is also bracketed here (bound to None, reset on exit) so the value
    require_principal sets is always restored without relying on the
    server copying the contextvars context per request. A closure-based
    pure ASGI middleware is used (not BaseHTTPMiddleware) so the
    contextvars are set in the same task that runs the endpoint and
    exception handlers.

    Args:
        app: The downstream ASGI application to wrap.

    Returns:
        An ASGI application that binds request context then delegates.
    """

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the request id, inject the header, then delegate."""
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        request_id = IdFactory().new_id("request")
        id_token = bind_request_id(request_id)
        user_token = bind_request_user(None)
        run_token = bind_run_id(None)
        pre_recorded_token = bind_pre_recorded_task_id(None)

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
            reset_request_var(pre_recorded_token)
            reset_request_var(run_token)
            reset_request_var(user_token)
            reset_request_var(id_token)

    return asgi


def _nearest_existing(path: Path) -> Path:
    """Return the closest existing ancestor of a path.

    Args:
        path: Filesystem path to walk upward from.

    Returns:
        The path itself or the nearest existing parent directory.
    """
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor or ".")


def _store_path_writable(raw_path: str) -> bool:
    """Verify a SQLite store path's directory is writable.

    The check never creates files or directories so readiness probes
    stay side-effect free.

    Args:
        raw_path: Configured SQLite store path.

    Returns:
        True when the nearest existing ancestor directory is writable.
    """
    parent = Path(raw_path).expanduser().resolve().parent
    return os.access(_nearest_existing(parent), os.W_OK)


async def _discover_interop_target(
    target: InteropTarget,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig,
    cache: DiscoveryCache,
) -> DiscoveryResult:
    """Discover one configured target through its metadata-only seam."""
    if target.kind == "mcp":
        return await discover_external_mcp_capabilities(
            target.id,
            registry=registry,
            sensitive_config=sensitive_config,
            cache=cache,
        )
    return await discover_external_a2a_capabilities(
        target.id,
        registry=registry,
        sensitive_config=sensitive_config,
        cache=cache,
    )


async def _discover_interop_targets(
    registry: InteropRegistry,
    *,
    sensitive_config: SensitiveConfig,
    caches: dict[str, DiscoveryCache],
) -> DiscoveryResult:
    """Discover every target while isolating failures per target id."""

    async def _one(target_id: str) -> DiscoveryResult:
        target = registry.require_target(target_id)
        cache = get_or_create_discovery_cache(
            caches,
            target,
            max_entries=ApiConfig().INTEROP_CACHE_MAX_ENTRIES,
        )
        return await _discover_interop_target(
            target,
            registry=registry,
            sensitive_config=sensitive_config,
            cache=cache,
        )

    results = await asyncio.gather(
        *(_one(target_id) for target_id in registry.target_ids()),
        return_exceptions=True,
    )
    data: list[Any] = []
    errors: list[DiscoveryError] = []
    for target_id, result in zip(registry.target_ids(), results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            target = registry.require_target(target_id)
            _LOGGER.warning(
                "interop capability discovery failed: %s",
                result.__class__.__name__,
            )
            errors.append(
                DiscoveryError(target.id, target.kind, "discovery_failed")
            )
            continue
        data.extend(result.data)
        errors.extend(result.errors)
    return DiscoveryResult(data=tuple(data), errors=tuple(errors))


def _interop_result_body(result: DiscoveryResult) -> dict[str, Any]:
    """Serialize only the shared capability DTO and safe error fields."""
    return {
        "object": "list",
        "data": [item.model_dump() for item in result.data],
        "errors": [
            {
                "target_id": item.target_id,
                "kind": item.kind,
                "code": item.code,
            }
            for item in result.errors
        ],
    }


async def _reconcile_run_task_logs(run_id: str, debug: bool) -> dict[str, Any]:
    """Compatibility seam for owner-scoped task-log reconciliation."""
    return await run_lifecycle.reconcile_run_task_logs(
        run_id,
        debug,
        fetch=_fetch_owner_run,
        reconcile=reconcile_task_log,
        strip=strip_agent_result,
    )


@asynccontextmanager
async def _http_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Own the process-wide shared ``AsyncClient`` for the API lifetime.

    The shared client carries a keep-alive connection pool used by
    every agent call site that opens ``get_async_client(timeout=...)``;
    initialising it once here avoids a per-request TLS handshake on
    the high-frequency LLM / retrieval paths. Teardown is wrapped in
    ``try / finally`` so a startup error never prevents the rest of
    the FastAPI shutdown chain from running.
    """
    init_shared_client()
    try:
        yield
    finally:
        await aclose_shared_client()
        await aclose_gauss_pool()


# pylint: disable=too-many-arguments,too-many-locals,too-many-statements
# create_app is a FastAPI factory that wires every route + dependency
# in one closure; the nested route handlers each accumulate request
# validation -> DB lookup -> response assembly inline. Splitting per
# module triples dependency-injection boilerplate. The disable runs
# to EOF since create_app is the last function in the file.
# See docs/development/lint-exemptions.md.
def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        Configured FastAPI app exposing liveness/readiness probes and the
        unified error envelope. Authenticated routes are added by later
        API layers.
    """
    configure_logging()
    app = FastAPI(
        title="Phytomni HTTP API",
        version=__version__,
        lifespan=_http_lifespan,
    )
    app.add_middleware(request_context_middleware)
    rate_limit = make_rate_limiter()
    interop_registry: InteropRegistry | None = None
    interop_sensitive_config: SensitiveConfig | None = None
    interop_caches: dict[str, DiscoveryCache] = {}
    memory_store: MemoryStore | None = None

    def get_memory_store() -> MemoryStore:
        """Lazily open the local memory store for an enabled deployment."""
        nonlocal memory_store
        if memory_store is None:
            try:
                config = ApiConfig()
                memory_store = MemoryStore(
                    config.MEMORY_DB_PATH,
                    policy=memory_policy_from_config(config),
                )
            except (MemorySchemaError, OSError, sqlite3.Error, ValueError):
                _LOGGER.warning("memory store unavailable")
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from None
        return memory_store

    async def authorized(
        principal: ApiPrincipal = Depends(require_principal),
    ) -> ApiPrincipal:
        """Authenticate, then enforce the per-key request budget."""
        limit = ApiConfig().API_RATE_LIMIT_PER_MIN
        retry_after = rate_limit(principal.key_prefix, limit)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return principal

    def require_scope(
        *needed: str,
    ) -> Callable[..., Awaitable[ApiPrincipal]]:
        """Build a dependency requiring the caller to hold scopes.

        Runs after ``authorized`` (authentication + rate limit), then
        checks the granted scopes, raising 403 (distinct from the
        auth-layer 401) when a required scope is missing. An all-access
        key (an empty scope set) satisfies every requirement.
        """

        async def _scoped(
            caller: ApiPrincipal = Depends(authorized),
        ) -> ApiPrincipal:
            if not scopes_satisfy(caller.scopes, needed):
                raise HTTPException(
                    status_code=403, detail="insufficient scope"
                )
            return caller

        return _scoped

    if ApiConfig().INTEROP_ENABLED:

        @app.get("/v1/interop/capabilities")
        async def list_interop_capabilities(
            request: Request,
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> JSONResponse:
            """List sanitized metadata for operator-approved targets."""
            nonlocal interop_registry, interop_sensitive_config
            del principal
            if request.query_params:
                raise HTTPException(
                    status_code=400,
                    detail="interop capabilities accepts no query parameters",
                )
            if interop_registry is None:
                try:
                    interop_sensitive_config = SensitiveConfig.load()
                    interop_registry = load_interop_registry(
                        ApiConfig(), interop_sensitive_config
                    )
                except (
                    InteropRegistryError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValidationError,
                    ValueError,
                ) as exc:
                    _LOGGER.warning(
                        "interop registry unavailable: %s",
                        exc.__class__.__name__,
                    )
                    return _error_response(503, "interop registry unavailable")
            if not interop_registry.enabled:
                return _error_response(404, "interop capabilities unavailable")
            assert interop_sensitive_config is not None
            result = await _discover_interop_targets(
                interop_registry,
                sensitive_config=interop_sensitive_config,
                caches=interop_caches,
            )
            return JSONResponse(_interop_result_body(result))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Return a dependency-free liveness signal."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Return readiness after checking local store paths."""
        config = ApiConfig()
        checks = {
            "api_keys_db": _store_path_writable(config.API_KEYS_DB_PATH),
            "tasks_db": _store_path_writable(config.API_TASKS_DB_PATH),
        }
        if not all(checks.values()):
            return _error_response(
                503, "one or more local stores are not writable"
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "checks": checks},
        )

    @app.get("/v1/models")
    async def list_models(
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """List the chat-like model ids (OpenAI convention)."""
        del principal  # Auth side-effect only.
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "object": "model",
                        "owned_by": "phytomni",
                    }
                    for model_id in MODEL_TO_TOOL
                ],
            }
        )

    if ApiConfig().MEMORY_ENABLED:

        @app.post(
            "/v1/memories",
            status_code=201,
            response_model=MemoryResponse,
        )
        async def create_memory(
            payload: MemoryCreateRequest,
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryResponse:
            """Create one memory in the authenticated user's namespace."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            write = _memory_write(owner, payload)
            try:
                with memory_audit_context(owner, current_request_id()):
                    record = get_memory_store().create(write)
            except MemoryPolicyError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return _memory_response(record)

        @app.get(
            "/v1/memories",
            response_model=MemoryListResponse,
        )
        async def list_memories(
            kind: str | None = None,
            limit: int | None = None,
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryListResponse:
            """List live memories owned by the authenticated user."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            try:
                records = get_memory_store().list(
                    owner, kind=kind, limit=limit
                )
            except MemoryPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory query"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return MemoryListResponse(
                data=[_memory_response(record) for record in records]
            )

        @app.get(
            "/v1/memories/export",
            response_model=MemoryExportResponse,
        )
        async def export_memories(
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryExportResponse:
            """Export live memories owned by the authenticated user."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            try:
                records = get_memory_store().export(owner)
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory export"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return MemoryExportResponse(
                data=[_memory_response(record) for record in records]
            )

        @app.get(
            "/v1/memories/audit",
            response_model=MemoryAuditListResponse,
        )
        async def list_memory_audit(
            user_id: str | None = None,
            operation: str | None = None,
            memory_id: str | None = None,
            limit: int = 100,
            offset: int = 0,
            _admin: None = Depends(require_service_principal),
        ) -> MemoryAuditListResponse:
            """List digest-only memory mutations for service operators."""
            del _admin
            try:
                records = get_memory_store().list_audit(
                    user_id=user_id,
                    operation=operation,
                    memory_id=memory_id,
                    limit=limit,
                    offset=offset,
                )
            except (MemoryPolicyError, ValidationError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return MemoryAuditListResponse(
                data=[_memory_audit_response(record) for record in records]
            )

        @app.get(
            "/v1/memories/{memory_id}",
            response_model=MemoryResponse,
        )
        async def get_memory(
            memory_id: str,
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryResponse:
            """Return one live memory only when it belongs to the caller."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            try:
                record = get_memory_store().get(owner, memory_id)
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory id"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            if record is None:
                raise HTTPException(status_code=404, detail="memory not found")
            return _memory_response(record)

        @app.put(
            "/v1/memories/{memory_id}",
            response_model=MemoryResponse,
        )
        async def update_memory(
            memory_id: str,
            payload: MemoryUpdateRequest,
            if_match: str | None = Header(default=None, alias="If-Match"),
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryResponse:
            """Replace one memory when ``If-Match`` is its current revision."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            expected_revision = _memory_revision(if_match, required=True)
            assert expected_revision is not None
            write = _memory_write(owner, payload)
            try:
                with memory_audit_context(owner, current_request_id()):
                    record = get_memory_store().update(
                        owner,
                        memory_id,
                        write,
                        expected_revision=expected_revision,
                    )
            except MemoryNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail="memory not found"
                ) from exc
            except MemoryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except MemoryPolicyError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory id"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return _memory_response(record)

        @app.delete(
            "/v1/memories/{memory_id}",
            response_model=MemoryDeleteResponse,
        )
        async def delete_memory(
            memory_id: str,
            if_match: str | None = Header(default=None, alias="If-Match"),
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> MemoryDeleteResponse:
            """Delete one caller-owned memory, idempotently."""
            del principal
            owner = current_request_user()
            if not owner:
                raise HTTPException(
                    status_code=401, detail="user context missing"
                )
            expected_revision = _memory_revision(if_match, required=False)
            try:
                with memory_audit_context(owner, current_request_id()):
                    deleted = get_memory_store().delete(
                        owner,
                        memory_id,
                        expected_revision=expected_revision,
                    )
            except MemoryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid memory id"
                ) from exc
            except (MemoryStoreError, OSError, sqlite3.Error) as exc:
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from exc
            return MemoryDeleteResponse(id=memory_id, deleted=deleted)

    @app.post(
        "/v1/api-keys",
        status_code=201,
        response_model=ApiKeyCreateResponse,
    )
    async def issue_api_key(
        payload: ApiKeyCreateRequest,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyCreateResponse:
        """Mint a per-user API key for the upstream service.

        The plaintext key is shown exactly once in the response. The
        service-token dependency is the only gate so a leaked user key
        cannot escalate to issuance.
        """
        del _admin  # Auth side-effect only.
        expires_at: datetime | None = None
        if payload.expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(
                days=payload.expires_days
            )
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        created = store.create(
            user_id=payload.user_id,
            name=payload.name,
            expires_at=expires_at,
        )
        return ApiKeyCreateResponse(
            api_key=created.api_key,
            prefix=created.prefix,
            user_id=created.user_id,
            expires_at=(expires_at.isoformat() if expires_at else None),
        )

    @app.get("/v1/api-keys", response_model=ApiKeyListResponse)
    async def list_api_keys(
        user_id: str | None = None,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyListResponse:
        """List per-user API keys; ``user_id`` filters to one user."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        records = store.list(user_id=user_id)
        return ApiKeyListResponse(
            data=[
                ApiKeyRecordResponse(
                    user_id=record.user_id,
                    name=record.name,
                    prefix=record.prefix,
                    created_at=record.created_at,
                    revoked_at=record.revoked_at,
                    last_used_at=record.last_used_at,
                    expires_at=record.expires_at,
                    active=record.active,
                    scopes=sorted(record.scopes),
                )
                for record in records
            ],
        )

    @app.delete(
        "/v1/api-keys/{prefix}",
        response_model=ApiKeyDeleteResponse,
    )
    async def revoke_api_key(
        prefix: str,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyDeleteResponse:
        """Revoke an active key by its public prefix."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        deleted = store.revoke(prefix)
        return ApiKeyDeleteResponse(prefix=prefix, deleted=deleted)

    @app.get("/v1/relay/audit")
    async def list_relay_audit(
        _admin: None = Depends(require_service_principal),
        *,
        user_id: str | None = None,
        key_prefix: str | None = None,
        service: str | None = None,
        status_code: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        """List relay audit records, gated by the service token.

        Only the service token may read the audit trail; a per-user
        ``ptm_...`` key alone cannot. The returned records carry no key
        hash, salt, or plaintext key, only the public ``key_prefix``.
        """
        del _admin  # Auth side-effect only.
        config = ApiConfig()
        store = get_audit_store(config.RELAY_AUDIT_DB_PATH)
        records = store.query(
            RelayAuditQuery(
                user_id=user_id,
                key_prefix=key_prefix,
                service=service,
                status_code=status_code,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
        )
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    _relay_audit_record_to_dict(record, config)
                    for record in records
                ],
            }
        )

    @app.get("/v1/relay/audit/{request_id}")
    async def get_relay_audit(
        request_id: str,
        _admin: None = Depends(require_service_principal),
    ) -> JSONResponse:
        """Fetch relay audit records by request id, service-token gated."""
        del _admin  # Auth side-effect only.
        config = ApiConfig()
        store = get_audit_store(config.RELAY_AUDIT_DB_PATH)
        records = store.get_by_request_id(request_id)
        return JSONResponse(
            {
                "object": "list",
                "request_id": request_id,
                "data": [
                    _relay_audit_record_to_dict(record, config)
                    for record in records
                ],
            }
        )

    @app.post(
        "/v1/chat/completions",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def chat_completions(
        payload: ChatCompletionRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> Response:
        """Run a chat-like agent in an OpenAI-compatible shape.

        With ``stream=true`` and a streaming-capable model, returns a
        ``text/event-stream`` carrying ``data: {...}\\n\\n`` chunks
        plus a terminating ``data: [DONE]\\n\\n``; the non-stream
        path returns a JSON ``chat.completion`` envelope unchanged.
        """
        del principal  # Auth side-effect; identity flows via contextvar.
        tool_name = tool_for_model(payload.model)
        if tool_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"model not found: {payload.model}",
            )
        obs_files = payload.obs_file_list or []
        accepts_obs = tool_accepts_obs(tool_name)
        if obs_files and not accepts_obs:
            raise HTTPException(
                status_code=400,
                detail=f"model {payload.model} does not accept "
                "obs_file_list",
            )
        try:
            user_query = flatten_messages(payload.messages)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user_query, resolve_meta = await resolve_chat_query(
            raw_query=user_query,
            resolve_flag=bool(payload.resolve_gene_id),
            tool_name=tool_name,
            brief_gene_resolver=resolve_brief_gene_user_query,
        )
        arguments: dict[str, object] = {"user_query": user_query}
        if accepts_obs:
            arguments["obs_file_list"] = obs_files
        if payload.stream:
            return await _stream_chat_response(
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
                user_query=user_query,
            )
        if tool_name == "ReviewAgent":
            return await _review_chat_completion_response(
                payload=payload,
                arguments=arguments,
                user_query=user_query,
            )
        envelope = await invoke_tool_enveloped(tool_name, arguments)
        formatted_dict = asdict(envelope.formatted)
        if resolve_meta:
            existing_meta = formatted_dict.get("metadata") or {}
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
        envelope_dict = {
            "formatted": formatted_dict,
            "raw": envelope.raw,
        }
        agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
        chat_run_id: str | None = None
        if agent_slug is not None:
            chat_run_id = _record_sync_run(
                agent=agent_slug,
                owner=current_request_user() or "anonymous",
                result=envelope_dict,
                request_info=RunRequestInfo(
                    dialogue_id=payload.dialogue_id,
                    query=user_query,
                    tool_name=tool_name,
                    model=payload.model,
                    request_json=payload.model_dump_json(),
                ),
            )
        completion = to_chat_completion(
            formatted_dict,
            envelope.raw,
            payload.model,
        )
        # Expose the Bot-side run id so Web can join chat completions
        # against ``GET /v1/runs?dialogue_id=...`` without relying on
        # the OpenAI ``chatcmpl-*`` provider id. ``None`` means the
        # registry write failed; surface that explicitly rather than
        # silently degrading (mirrors the remote-agent
        # ``degraded_tracking`` signal).
        completion["run_id"] = chat_run_id
        if chat_run_id is None and agent_slug is not None:
            completion["degraded_tracking"] = True
        if not resolve_debug(payload.debug):
            completion = strip_chat_completion(completion)
        return JSONResponse(completion)

    @app.get("/v1/agents")
    async def list_agents(
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """List the agents reachable via ``/v1/agents/{slug}/runs``."""
        del principal
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "slug": slug,
                        "tool": tool,
                        "origin": (
                            "remote"
                            if slug in _REMOTE_AGENT_SLUGS
                            else "local"
                        ),
                        "legacy_aliases": _LEGACY_ALIASES.get(tool, []),
                        "capabilities": serialize_agent_capability(slug),
                    }
                    for slug, tool in _AGENT_SLUG_TO_TOOL.items()
                ],
            }
        )

    @app.post(
        "/v1/agents/{agent}/runs",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def create_agent_run(
        agent: str,
        payload: AgentRunRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """Invoke one agent by slug and return its agent.run envelope."""
        del principal
        body, status_code = await _invoke_agent_run(
            agent=agent,
            arguments=payload.arguments,
            dialogue_id=payload.dialogue_id,
            debug=resolve_debug(payload.debug),
            request_json=payload.model_dump_json(),
        )
        return JSONResponse(body, status_code=status_code)

    @app.post(
        "/v1/query/route",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def route_query(
        payload: ExpertQueryRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """Autonomously route a query to an agent and return its run."""
        del principal
        body, status_code = await _route_expert_query(
            payload, debug=resolve_debug(None)
        )
        return JSONResponse(body, status_code=status_code)

    @app.post(
        "/v1/files",
        status_code=201,
        response_model=FileUploadResponse,
    )
    async def upload_file(
        request: Request,
        file: UploadFile = File(...),
        purpose: UploadPurpose = Form("agent_context"),
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> FileUploadResponse | JSONResponse:
        """Accept one multipart file upload and store it in OBS.

        Pre-checks ``Content-Length`` so oversize requests are rejected
        before the body is buffered; falls back to a post-read size
        guard inside ``upload_user_file`` so missing or falsified
        Content-Length (e.g. chunked transfer) is still caught. The
        sanitized filename, byte length, and public OBS path are
        returned in a ``FileUploadResponse`` shape with ``path`` aliased
        to ``obs_path`` so existing chat-ai code that already reads
        ``path`` from the legacy upload bridge can plug in unchanged.
        """
        return await handle_file_upload(
            request=request,
            file=file,
            purpose=purpose,
            user_id=principal.user_id,
            error_response=_error_response,
        )

    @app.get("/v1/runs/{run_id}/logs")
    async def get_run_logs(
        run_id: str,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Return reconciled task logs for a run.

        Fetches the run to verify ownership, then reconciles logs for
        each task in the run. Default mode strips the raw handler
        payload from each task log; pass ``debug=true`` to include it.
        """
        del principal
        return JSONResponse(
            await _reconcile_run_task_logs(run_id, resolve_debug(debug))
        )

    @app.get("/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Return one owner-scoped run record by id.

        Default mode strips the raw handler payload from result;
        pass ``debug=true`` to include it.
        """
        del principal
        record = await _fetch_owner_run(run_id, debug=resolve_debug(debug))
        if not resolve_debug(debug) and isinstance(record.get("result"), dict):
            record = {
                **record,
                "result": strip_agent_result(record["result"]),
            }
        return JSONResponse(record)

    @app.post("/v1/runs/{run_id}/a2ui-actions")
    async def post_a2ui_action(
        run_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Resume a paused Chat A2UI run from a Web action envelope."""
        del principal
        if not ApiConfig().A2UI_ENABLED:
            raise HTTPException(status_code=403, detail="a2ui disabled")
        try:
            body = await read_a2ui_action_request(request)
        except A2uiPayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except A2uiPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response_body, status_code = await _resume_a2ui_run(
            run_id=run_id,
            body=body,
            debug=resolve_debug(debug),
        )
        try:
            ensure_a2ui_response_size(
                response_body,
                max_bytes=ApiConfig().A2UI_MAX_RESPONSE_BYTES,
            )
        except A2uiPayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except A2uiPayloadError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(response_body, status_code=status_code)

    @app.post("/v1/runs/{thread_id}/resume")
    async def resume_run(
        thread_id: str,
        body: ResumeRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Resume a paused ReviewAgent run by LangGraph thread id."""
        del principal
        response_body, status_code = await _resume_review_run(
            thread_id=thread_id,
            payload=body,
            debug=resolve_debug(debug),
        )
        return JSONResponse(response_body, status_code=status_code)

    @app.get("/v1/runs")
    async def list_runs(
        principal: ApiPrincipal = Depends(require_scope("agents")),
        *,
        status: str | None = None,
        agent: str | None = None,
        origin: str | None = None,
        user_id: str | None = None,
        dialogue_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 10,
        offset: int = 0,
        debug: bool = False,
        authorization: str | None = Header(default=None),
        x_service_token: str | None = Header(
            default=None, alias="X-Service-Token"
        ),
    ) -> JSONResponse:
        """List runs with owner-only or service-token-delegated scoping.

        Without ``user_id`` the route returns the authenticated user's
        runs only. With ``user_id`` it requires a valid service token
        in addition to the user key, then scopes the listing to that
        user instead of the caller — the path Phytomni-Web Go uses to
        render history pages for any tenant. Acts as the lazy GC
        trigger via ``_purge_expired_runs_best_effort``.

        ``dialogue_id`` runs as a server-side ``WHERE`` predicate
        before ``limit`` / ``offset`` so a chat-ai history query for
        one dialogue always retrieves every matching row regardless
        of the caller's total run count.

        Default mode strips the raw handler payload from each result;
        pass ``debug=true`` to include it.
        """
        del principal
        is_service = is_service_token_valid(authorization, x_service_token)
        if user_id is not None and not is_service:
            raise HTTPException(
                status_code=403,
                detail=("user_id query parameter requires the service token"),
            )
        owner = (
            user_id
            if user_id is not None
            else (current_request_user() or "anonymous")
        )
        body = _list_owner_runs(
            owner=owner,
            status=status,
            agent=agent,
            origin=origin,
            dialogue_id=dialogue_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            debug=resolve_debug(debug),
        )
        if not resolve_debug(debug):
            data = body.get("data")
            if isinstance(data, list):
                body = {
                    **body,
                    "data": [_strip_run_result(r) for r in data],
                }
        return JSONResponse(body)

    a2a_config = ApiConfig()
    if a2a_config.A2A_ENABLED:
        public_base_url = a2a_config.A2A_PUBLIC_BASE_URL
        assert public_base_url is not None
        a2a_handler = A2ARequestHandler(
            invoke_agent_run=_invoke_agent_run,
            options=A2AHandlerOptions(
                invoke_agent_stream=invoke_tool_streamed,
                record_a2a=_record_a2a_registration,
                get_a2a_task=_get_a2a_task,
                resume_a2a=_resume_a2a_task,
            ),
            tool_to_agent={
                tool_name: agent
                for agent, tool_name in _AGENT_SLUG_TO_TOOL.items()
            },
            select_agent=select_agent_tool,
        )
        a2a_route = create_jsonrpc_routes(a2a_handler, "/a2a")[0]

        @app.get("/.well-known/agent-card.json")
        async def a2a_agent_card() -> JSONResponse:
            """Return the public A2A card when the feature is enabled."""
            card = build_agent_card(public_base_url)
            return JSONResponse(json_format.MessageToDict(card))

        @app.post("/a2a")
        async def a2a_jsonrpc(
            request: Request,
            a2a_version: str | None = Header(
                default=None, alias="A2A-Version"
            ),
            principal: ApiPrincipal = Depends(require_scope("agents")),
        ) -> Response:
            """Authenticate and dispatch one A2A v1 JSON-RPC request."""
            del principal
            if a2a_version != "1.0":
                raise HTTPException(
                    status_code=400,
                    detail="A2A-Version must be exactly 1.0",
                )
            return await a2a_route.endpoint(request)

    app.include_router(create_relay_router())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Render HTTP exceptions through the unified envelope."""
        message = exc.detail if isinstance(exc.detail, str) else "error"
        return _error_response(
            exc.status_code, message, getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        """Render request validation errors as 422 envelopes."""
        return _error_response(422, "request validation failed")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request, _exc: Exception
    ) -> JSONResponse:
        """Render unexpected errors as 500 envelopes."""
        return _error_response(500, "internal server error")

    return app
