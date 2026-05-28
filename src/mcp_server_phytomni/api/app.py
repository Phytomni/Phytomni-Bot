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
# without reducing total complexity. See docs/lint-exemptions.md.

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
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
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..agents.brief_gene.resolve_query import (
    BriefGeneResolveError,
    resolve_brief_gene_user_query,
)
from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..common.logging_config import configure_logging
from ..config.defaults import ApiConfig, BriefGeneConfig
from ..config.settings import SensitiveConfig
from ..mcp.app import invoke_tool_enveloped, invoke_tool_streamed
from ..mcp.result_formatting import (
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)
from ..runtime.request_context import (
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
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..runtime.task_reconcile import reconcile_task_log
from ..storage.path_policy import IdFactory
from .admin_auth import is_service_token_valid, require_service_principal
from .auth import ApiPrincipal, get_key_store, require_principal
from .file_upload import handle_file_upload
from .openai_mapping import (
    MODEL_TO_TOOL,
    flatten_messages,
    to_chat_completion,
    to_chat_completion_chunks,
    tool_accepts_obs,
    tool_accepts_resolve_gene_id,
    tool_accepts_stream,
    tool_for_model,
)
from .ratelimit import make_rate_limiter
from .schemas import (
    AgentRunRequest,
    ApiErrorDetail,
    ApiErrorResponse,
    ApiKeyCreateRequest,
    ChatCompletionRequest,
    UploadPurpose,
)

__all__ = ["create_app"]

_ERROR_TYPES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "unprocessable_entity",
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


def _purge_expired_runs_best_effort() -> None:
    """Run a single ``RunRegistry.purge_expired`` pass, swallowing errors.

    Every API write path (sync chat completions, native agent runs,
    and the registry listing) drives the lazy GC by calling this
    helper so an expired row never outlives its TTL. A SQLite / OS
    failure must never propagate — the user-facing write already
    succeeded and the next request can re-trigger the purge.
    """
    try:
        RunRegistry(resolve_tasks_db_path()).purge_expired()
    except (sqlite3.Error, OSError):
        pass


def _extract_answer(result: Any) -> Optional[str]:
    """Pull a display-ready answer string from a stored run result.

    Tolerates the two envelope shapes the API writes today: the chat /
    agent-run path stores ``{"formatted": {"answer": ...}, "raw": ...}``
    while older sync writers may carry a top-level ``answer``. Returns
    ``None`` when neither shape carries a string answer so chat-ai can
    render an "ongoing" placeholder without crashing.
    """
    if not isinstance(result, dict):
        return None
    formatted = result.get("formatted")
    if isinstance(formatted, dict):
        candidate = formatted.get("answer")
        if isinstance(candidate, str):
            return candidate
    candidate = result.get("answer")
    if isinstance(candidate, str):
        return candidate
    return None


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    """Flatten a ``RunRecord`` into the JSON envelope the API returns.

    Unpacks ``spec`` (identity bundle), ``timestamps`` (lifecycle
    bundle), and ``request_info`` (per-request metadata bundle) so the
    on-wire shape stays a flat object rather than the nested dataclass
    tree, and serialises ``task_ids`` as a list so clients consume it
    as a JSON array. The ``answer`` shortcut surfaces the formatted
    response text directly so chat-ai's history page does not have to
    descend into ``result.formatted.answer`` per row.

    Args:
        record: The ``RunRegistry`` record to flatten.

    Returns:
        A JSON-serialisable dict.
    """
    info = record.request_info
    return {
        "run_id": record.spec.run_id,
        "agent": record.spec.agent,
        "origin": record.spec.origin,
        "user_id": record.spec.user_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
        "created_at": record.timestamps.created_at,
        "updated_at": record.timestamps.updated_at,
        "expires_at": record.timestamps.expires_at,
        "task_ids": list(record.task_ids),
        "dialogue_id": info.dialogue_id,
        "query": info.query,
        "tool_name": info.tool_name,
        "model": info.model,
        "answer": _extract_answer(record.result),
    }


def _stream_chat_completion(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Wrap ``invoke_tool_streamed`` + SSE shaper + run-record finalize.

    The wrapper is an async generator: each emitted SSE line forwards
    immediately to the client (no buffering), and once the upstream
    stream drains (or aborts) the run record is written exactly once
    from the ``finally`` block. The recorded ``result`` carries
    stream-mode markers rather than the aggregated response content
    — buffering the entire stream just to populate the record would
    defeat the primitive's per-chunk design and ties recorded volume
    to LLM output size for no caller benefit. The ``completed`` flag
    distinguishes a normal drain from a client-disconnect / mid-stream
    error so the run-history view can surface partial calls.

    Auth, rate-limit, request-id, and OBS argument prep all happen
    before this helper is called, mirroring the non-stream branch.
    """
    raw_chunks = invoke_tool_streamed(tool_name, arguments)
    sse_lines = to_chat_completion_chunks(raw_chunks, payload.model)
    agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
    owner = current_request_user() or "anonymous"

    async def _wrapped() -> AsyncIterator[str]:
        completed = False
        try:
            async for line in sse_lines:
                yield line
            completed = True
        finally:
            if agent_slug is not None:
                _record_sync_run(
                    agent=agent_slug,
                    owner=owner,
                    result={
                        "formatted": {"answer": "[streamed]"},
                        "raw": None,
                        "stream": True,
                        "completed": completed,
                    },
                    request_info=RunRequestInfo(
                        dialogue_id=payload.dialogue_id,
                        query=user_query,
                        tool_name=tool_name,
                        model=payload.model,
                        request_json=payload.model_dump_json(),
                    ),
                )

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


async def _maybe_resolve_brief_gene_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    tool_name: Optional[str],
    agent_slug: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve free-form text into a gene id when ``resolve_gene_id`` is on.

    Both the OpenAI-compatible chat-completions route and the native
    ``/v1/agents/brief_gene/runs`` route funnel through here so the
    flag has identical semantics on both surfaces: BriefGene-only,
    explicit 400 on misuse, explicit 400 on resolver failure, and a
    deterministic metadata patch describing the rewrite.

    Args:
        raw_query: The original ``user_query`` text from the request.
        resolve_flag: Whether the caller opted into LLM preprocessing.
        tool_name: MCP tool name when the chat-completions path resolved
            it; ``None`` for the native runs path which gates on slug.
        agent_slug: Native agent slug when the runs path supplied one;
            ``None`` for the chat-completions path which gates on tool.

    Returns:
        Tuple of ``(user_query_for_tool, metadata_patch)``. The metadata
        patch is empty when the flag is off and otherwise carries the
        ``original_query`` / ``resolved_gene_id`` / ``resolve_gene_id``
        keys for caller observability.

    Raises:
        HTTPException: 400 when the flag is set on a non-BriefGene
            tool/slug or the resolver raises ``BriefGeneResolveError``.
    """
    if not resolve_flag:
        return raw_query, {}
    brief_tool = tool_name is not None and tool_accepts_resolve_gene_id(
        tool_name
    )
    brief_slug = agent_slug == "brief_gene"
    if not (brief_tool or brief_slug):
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for BriefGene calls",
        )
    try:
        result = await resolve_brief_gene_user_query(
            raw_query,
            brief_config=BriefGeneConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except BriefGeneResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.gene_id, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolve_gene_id": True,
    }


# pylint: disable=too-many-locals
# Request validation -> DB lookup -> reconciliation -> response
# assembly inline; helpers would require 5+ context args each.
# See docs/lint-exemptions.md.
async def _invoke_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: Optional[str] = None,
    request_json: Optional[str] = None,
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
    remote agent regardless of formatter shape, **except** on an
    analyst dedup-hit passthrough: when ``result["dedup_hit"]`` is
    ``True`` the chokepoint deliberately skips the registry write
    so the prior caller's run id stays authoritative, and this
    endpoint returns ``id=null`` with ``task_ids=[]`` while the
    prior ``task_id`` remains available under ``result["task_id"]``
    for the caller to poll directly.

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
    resolve_flag = bool(arguments.pop("resolve_gene_id", False))
    resolve_meta: dict[str, Any] = {}
    if resolve_flag:
        raw_query = arguments.get("user_query")
        if not isinstance(raw_query, str) or not raw_query.strip():
            raise HTTPException(
                status_code=400,
                detail="user_query is required when resolve_gene_id is true",
            )
        resolved, resolve_meta = await _maybe_resolve_brief_gene_query(
            raw_query=raw_query,
            resolve_flag=True,
            tool_name=None,
            agent_slug=agent,
        )
        arguments["user_query"] = resolved
    envelope = await invoke_tool_enveloped(tool_name, arguments)
    formatted_dict = asdict(envelope.formatted)
    if resolve_meta:
        existing_meta = formatted_dict.get("metadata") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
    result = {"formatted": formatted_dict, "raw": envelope.raw}
    response_result = result if debug else strip_agent_result(result)
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
    if agent in _REMOTE_AGENT_SLUGS:
        run_id, task_ids = _resolve_remote_run(owner)
        _stamp_remote_request_info(
            run_id=run_id, owner=owner, request_info=request_info
        )
        _purge_expired_runs_best_effort()
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
        # until a manual reconcile is run. Without this flag a client
        # cannot tell the failure case apart from a legitimate
        # analyst dedup-hit, which also returns ``id=None`` /
        # ``task_ids=[]`` but for a benign reason and routes the
        # caller to ``result["task_id"]`` instead.
        if current_recorder_degraded():
            body["degraded_tracking"] = True
        return body, 202
    run_id = _record_sync_run(
        agent=agent,
        owner=owner,
        result=result,
        request_info=request_info,
    )
    body = {
        "id": run_id,
        "object": "agent.run",
        "agent": agent,
        "status": "succeeded",
        "task_ids": [],
        "result": response_result,
    }
    return body, 200


def _resolve_remote_run(owner: str) -> tuple[Optional[str], list[str]]:
    """Read the chokepoint's run id from contextvar, then list its tasks.

    The submit chokepoint in ``mcp/handlers`` calls ``bind_run_id``
    after it writes the runs row plus its N child task rows, so the
    HTTP layer can recover the run identity directly from the
    per-request contextvar — no formatter-specific metadata key
    (analyst's ``task_id`` vs deep_genome's ``server_id`` vs
    research's missing entry) is consulted. The child task ids are
    then sourced from ``RunRegistry.get_run`` so a multi-task
    submission returns every task id the chokepoint persisted, not
    just the primary one.

    Args:
        owner: Authenticated user id used for the registry read.

    Returns:
        ``(run_id, task_ids)`` where ``run_id`` is ``None`` and
        ``task_ids`` is empty when the chokepoint did not bind a
        run id — either because the registry write failed midway
        (see ``runtime.submit_recorder.record_submitted_task``) or
        because the wrapper returned an analyst dedup-hit
        passthrough that intentionally skipped the write to preserve
        the prior caller's run id. Callers distinguish the two via
        ``current_recorder_degraded()``: ``True`` is the silent
        persistence-failure case and the surrounding
        ``_invoke_agent_run`` body adds ``degraded_tracking: True``;
        ``False`` plus ``result["dedup_hit"] is True`` is the
        transparent passthrough whose prior ``task_id`` is in
        ``result["task_id"]``.
    """
    run_id = current_run_id()
    if run_id is None:
        return None, []
    record = RunRegistry(resolve_tasks_db_path()).get_run(run_id, owner=owner)
    if record is None:
        return run_id, []
    return run_id, list(record.task_ids)


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
# query model triples the route boilerplate. See docs/lint-exemptions.md.
def _list_owner_runs(
    *,
    owner: str,
    status: Optional[str],
    agent: Optional[str],
    origin: Optional[str],
    dialogue_id: Optional[str],
    created_after: Optional[str],
    created_before: Optional[str],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Return one ``GET /v1/runs`` body scoped to ``owner``.

    Drives the lazy GC via ``_purge_expired_runs_best_effort`` (the
    same helper the sync chat and native agent run write paths use)
    so the registry stays bounded under listing-heavy and
    submission-heavy workloads alike.

    Args:
        owner: User id whose runs to return. Set by the route from
            ``current_request_user()`` for owner-only calls, or from
            the ``user_id`` query parameter for delegated calls that
            already passed the service-token check.
        status: Optional exact-match status filter.
        agent: Optional exact-match agent slug filter.
        origin: Optional exact-match origin filter.
        dialogue_id: Optional exact-match dialogue id filter. Runs
            before ``limit`` so a chat-ai history query for one
            dialogue always retrieves every matching row.
        created_after: Optional ISO-8601 lower bound (inclusive).
        created_before: Optional ISO-8601 upper bound (inclusive).
        limit: Max rows to return.
        offset: Rows to skip (paging).

    Returns:
        ``{"object", "data"}`` envelope with the flat run records.
    """
    _purge_expired_runs_best_effort()
    records = RunRegistry(resolve_tasks_db_path()).list_runs(
        owner=owner,
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
    )
    return {
        "object": "list",
        "data": [_run_record_to_dict(record) for record in records],
    }


# pylint: enable=too-many-arguments


async def _fetch_owner_run(run_id: str) -> dict[str, Any]:
    """Reconcile + flatten one ``GET /v1/runs/{run_id}`` request body.

    Args:
        run_id: Run id to fetch.

    Returns:
        Flat JSON-serialisable run envelope.

    Raises:
        HTTPException: 404 when the run is unknown or foreign-owned.
    """
    owner = current_request_user() or "anonymous"
    registry = RunRegistry(resolve_tasks_db_path())
    record = await registry.reconcile(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return _run_record_to_dict(record)


def _record_sync_run(
    *,
    agent: str,
    owner: str,
    result: dict[str, Any],
    request_info: Optional[RunRequestInfo] = None,
) -> Optional[str]:
    """Persist a terminal ``origin="local"`` run for a sync agent call.

    Mints a fresh ``run_id`` via ``IdFactory().new_id("run", agent)``
    and writes one ``runs`` row at terminal status ``"succeeded"`` so
    the upcoming ``/v1/runs/{id}`` and ``/v1/runs`` endpoints replay
    the formatted answer without re-invoking the agent. SQLite / OS
    failures are swallowed — a successful HTTP completion must never
    fail because the bookkeeping write hit the disk wrong.

    The MCP stdio path never reaches this helper (it does not enter
    the FastAPI request lifecycle), so the existing stdio MCP
    contract stays byte-equivalent.

    Args:
        agent: Public agent alias (e.g. ``"chat"``).
        owner: Authenticated user id (``"anonymous"`` for stdio).
        result: The formatted result dict (stored as JSON in
            ``result_json``).
        request_info: Per-request metadata captured at the HTTP
            boundary; ``None`` keeps every per-request column NULL.

    Returns:
        The minted ``run_id`` on a successful write, otherwise
        ``None``.
    """
    run_id = IdFactory().new_id("run", agent)
    try:
        RunRegistry(resolve_tasks_db_path()).create_run(
            RunSpec(
                run_id=run_id,
                user_id=owner,
                agent=agent,
                origin="local",
            ),
            outcome=RunOutcome(status="succeeded", result=result),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError):
        return None
    _purge_expired_runs_best_effort()
    return run_id


def _stamp_remote_request_info(
    *,
    run_id: Optional[str],
    owner: str,
    request_info: RunRequestInfo,
) -> None:
    """Back-fill request-info columns on a chokepoint-minted run row.

    Remote agents (analyst / deep_genome / research / design / network)
    have their run row created inside the submit chokepoint before the
    API layer can attach request metadata. Once the response returns
    and ``_resolve_remote_run`` recovers the run id, this helper
    updates the five per-request columns owner-scoped so the history
    page sees the same shape as sync runs. SQLite / OS failures are
    swallowed — the user already got their 202 response.

    Args:
        run_id: Run id minted by the chokepoint; ``None`` skips the
            write (analyst dedup-hit passthrough or chokepoint failure).
        owner: Authenticated user id used for the owner check.
        request_info: Field values to write.
    """
    if run_id is None:
        return
    try:
        RunRegistry(resolve_tasks_db_path()).update_request_info(
            run_id, owner=owner, request_info=request_info
        )
    except (sqlite3.Error, OSError):
        return


def _error_response(
    status_code: int,
    message: str,
    headers: Optional[Mapping[str, str]] = None,
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

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
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


async def _reconcile_run_task_logs(run_id: str, debug: bool) -> dict[str, Any]:
    """Reconcile task logs for every task in a run.

    Fetches the run to verify ownership, iterates its task ids, and
    returns a JSON-ready envelope with one reconciled log per task.
    When ``debug`` is False, the raw handler payload is stripped from
    each log via ``strip_agent_result`` so default-mode responses stay
    compact.

    Args:
        run_id: Run whose task logs are being reconciled.
        debug: When True, keep the raw handler payload in each log.

    Returns:
        ``{"run_id", "task_ids", "task_logs"}`` envelope ready for
        ``JSONResponse``.

    Raises:
        HTTPException: Propagated from ``_fetch_owner_run`` when the
            run is unknown or foreign-owned.
    """
    record = await _fetch_owner_run(run_id)
    task_ids = record.get("task_ids", [])
    task_logs: list[dict[str, Any]] = []
    for task_id in task_ids:
        log = await reconcile_task_log(task_id)
        if log is None:
            continue
        if not debug:
            log = strip_agent_result(log)
        task_logs.append(log)
    return {
        "run_id": run_id,
        "task_ids": task_ids,
        "task_logs": task_logs,
    }


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


# pylint: disable=too-many-arguments,too-many-locals,too-many-statements
# create_app is a FastAPI factory that wires every route + dependency
# in one closure; the nested route handlers each accumulate request
# validation -> DB lookup -> response assembly inline. Splitting per
# module triples dependency-injection boilerplate. The disable runs
# to EOF since create_app is the last function in the file.
# See docs/lint-exemptions.md.
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
        version="0.1.0",
        lifespan=_http_lifespan,
    )
    app.add_middleware(request_context_middleware)
    rate_limit = make_rate_limiter()

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
        principal: ApiPrincipal = Depends(authorized),
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

    @app.post("/v1/api-keys", status_code=201)
    async def issue_api_key(
        payload: ApiKeyCreateRequest,
        _admin: None = Depends(require_service_principal),
    ) -> JSONResponse:
        """Mint a per-user API key for the upstream service.

        The plaintext key is shown exactly once in the response. The
        service-token dependency is the only gate so a leaked user key
        cannot escalate to issuance.
        """
        del _admin  # Auth side-effect only.
        expires_at: Optional[datetime] = None
        if payload.expires_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=payload.expires_days
            )
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        created = store.create(
            user_id=payload.user_id,
            name=payload.name,
            expires_at=expires_at,
        )
        return JSONResponse(
            status_code=201,
            content={
                "object": "api_key",
                "api_key": created.api_key,
                "prefix": created.prefix,
                "user_id": created.user_id,
                "expires_at": (expires_at.isoformat() if expires_at else None),
            },
        )

    @app.get("/v1/api-keys")
    async def list_api_keys(
        user_id: Optional[str] = None,
        _admin: None = Depends(require_service_principal),
    ) -> JSONResponse:
        """List per-user API keys; ``user_id`` filters to one user."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        records = store.list(user_id=user_id)
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "user_id": record.user_id,
                        "name": record.name,
                        "prefix": record.prefix,
                        "created_at": record.created_at,
                        "revoked_at": record.revoked_at,
                        "last_used_at": record.last_used_at,
                        "expires_at": record.expires_at,
                        "active": record.active,
                    }
                    for record in records
                ],
            }
        )

    @app.delete("/v1/api-keys/{prefix}")
    async def revoke_api_key(
        prefix: str,
        _admin: None = Depends(require_service_principal),
    ) -> JSONResponse:
        """Revoke an active key by its public prefix."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        deleted = store.revoke(prefix)
        return JSONResponse(
            {
                "object": "api_key.deleted",
                "prefix": prefix,
                "deleted": deleted,
            }
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: ChatCompletionRequest,
        principal: ApiPrincipal = Depends(authorized),
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
        user_query, resolve_meta = await _maybe_resolve_brief_gene_query(
            raw_query=user_query,
            resolve_flag=bool(payload.resolve_gene_id),
            tool_name=tool_name,
        )
        arguments: dict[str, object] = {"user_query": user_query}
        if accepts_obs:
            arguments["obs_file_list"] = obs_files
        if payload.stream:
            if not tool_accepts_stream(tool_name):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"streaming is not supported for model "
                        f"{payload.model}"
                    ),
                )
            return _stream_chat_completion(
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
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
        if agent_slug is not None:
            _record_sync_run(
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
        if not resolve_debug(payload.debug):
            completion = strip_chat_completion(completion)
        return JSONResponse(completion)

    @app.get("/v1/agents")
    async def list_agents(
        principal: ApiPrincipal = Depends(authorized),
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
                    }
                    for slug, tool in _AGENT_SLUG_TO_TOOL.items()
                ],
            }
        )

    @app.post("/v1/agents/{agent}/runs")
    async def create_agent_run(
        agent: str,
        payload: AgentRunRequest,
        principal: ApiPrincipal = Depends(authorized),
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

    @app.post("/v1/files", status_code=201)
    async def upload_file(
        request: Request,
        file: UploadFile = File(...),
        purpose: UploadPurpose = Form("agent_context"),
        principal: ApiPrincipal = Depends(authorized),
    ) -> JSONResponse:
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
        principal: ApiPrincipal = Depends(authorized),
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
        principal: ApiPrincipal = Depends(authorized),
        debug: bool = False,
    ) -> JSONResponse:
        """Return one owner-scoped run record by id.

        Default mode strips the raw handler payload from result;
        pass ``debug=true`` to include it.
        """
        del principal
        record = await _fetch_owner_run(run_id)
        if not resolve_debug(debug) and isinstance(record.get("result"), dict):
            record = {
                **record,
                "result": strip_agent_result(record["result"]),
            }
        return JSONResponse(record)

    @app.get("/v1/runs")
    async def list_runs(
        principal: ApiPrincipal = Depends(authorized),
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        origin: Optional[str] = None,
        user_id: Optional[str] = None,
        dialogue_id: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        debug: bool = False,
        authorization: Optional[str] = Header(default=None),
        x_service_token: Optional[str] = Header(
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
        )
        if not resolve_debug(debug):
            data = body.get("data")
            if isinstance(data, list):
                body = {
                    **body,
                    "data": [_strip_run_result(r) for r in data],
                }
        return JSONResponse(body)

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
