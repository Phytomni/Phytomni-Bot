# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config.defaults import ApiConfig
from ..mcp.app import invoke_tool_formatted
from ..runtime.request_context import (
    bind_request_id,
    bind_request_user,
    current_request_id,
    current_request_user,
    reset_request_var,
)
from ..runtime.run_registry import RunRegistry, RunSpec
from ..runtime.task_manager import TaskManager, resolve_tasks_db_path
from ..storage.path_policy import IdFactory
from .auth import ApiPrincipal, require_principal
from .openai_mapping import (
    MODEL_TO_TOOL,
    flatten_messages,
    to_chat_completion,
    tool_accepts_obs,
    tool_for_model,
)
from .ratelimit import make_rate_limiter
from .schemas import (
    AgentRunRequest,
    ApiErrorDetail,
    ApiErrorResponse,
    ChatCompletionRequest,
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
# ``_records_submission`` chokepoint in ``mcp/handlers`` to write the
# runs row with ``origin="remote"``; the API layer instead reads
# ``tasks.run_id`` back via ``TaskManager.run_id_for_task``.
_REMOTE_AGENT_SLUGS = frozenset(
    {"analyst", "deep_genome", "research", "design", "network"}
)


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    """Flatten a ``RunRecord`` into the JSON envelope the API returns.

    Unpacks ``spec`` (identity bundle) and ``timestamps`` (lifecycle
    bundle) so the on-wire shape stays a flat object rather than the
    nested dataclass tree, and serialises ``task_ids`` as a list so
    clients consume it as a JSON array.

    Args:
        record: The ``RunRegistry`` record to flatten.

    Returns:
        A JSON-serialisable dict.
    """
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
    }


async def _invoke_agent_run(
    *, agent: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch one ``/v1/agents/{agent}/runs`` call and shape the body.

    Owns the slug -> tool lookup, the shared ``invoke_tool_formatted``
    call, and the origin-aware run id resolution: for remote agents the
    chokepoint has already written the row so we read ``tasks.run_id``
    back; for sync agents we mint and persist an ``origin="local"``
    terminal row via ``_record_sync_run``.

    Args:
        agent: Public agent alias (e.g. ``"chat"``).
        arguments: Tool-specific kwargs forwarded to the agent.

    Returns:
        The ``{"run_id", "result"}`` response body.

    Raises:
        HTTPException: 404 when the slug is unknown.
    """
    tool_name = _AGENT_SLUG_TO_TOOL.get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    formatted = await invoke_tool_formatted(tool_name, arguments)
    result = asdict(formatted)
    owner = current_request_user() or "anonymous"
    if agent in _REMOTE_AGENT_SLUGS:
        metadata = result.get("metadata") or {}
        task_id = metadata.get("task_id")
        run_id = (
            TaskManager(resolve_tasks_db_path()).run_id_for_task(task_id)
            if isinstance(task_id, str) and task_id
            else None
        )
    else:
        run_id = _record_sync_run(agent=agent, owner=owner, result=result)
    return {"run_id": run_id, "result": result}


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
    *, agent: str, owner: str, result: dict[str, Any]
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
            status="succeeded",
            result=result,
        )
    except (sqlite3.Error, OSError):
        return None
    return run_id


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

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
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


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        Configured FastAPI app exposing liveness/readiness probes and the
        unified error envelope. Authenticated routes are added by later
        API layers.
    """
    app = FastAPI(title="Phytomni HTTP API", version="0.1.0")
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

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: ChatCompletionRequest,
        principal: ApiPrincipal = Depends(authorized),
    ) -> JSONResponse:
        """Run a chat-like agent in an OpenAI-compatible shape."""
        del principal  # Auth side-effect; identity flows via contextvar.
        if payload.stream:
            raise HTTPException(
                status_code=400,
                detail="streaming is not supported",
            )
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
        arguments: dict[str, object] = {"user_query": user_query}
        if accepts_obs:
            arguments["obs_file_list"] = obs_files
        formatted = await invoke_tool_formatted(tool_name, arguments)
        result = asdict(formatted)
        agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
        if agent_slug is not None:
            _record_sync_run(
                agent=agent_slug,
                owner=current_request_user() or "anonymous",
                result=result,
            )
        return JSONResponse(
            to_chat_completion(
                result,
                payload.model,
                extra_keys=(
                    "follow_up_questions",
                    "references",
                    "metadata",
                ),
            )
        )

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
        """Invoke one agent by slug and return its run id + result."""
        del principal
        return JSONResponse(
            await _invoke_agent_run(agent=agent, arguments=payload.arguments)
        )

    @app.get("/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        principal: ApiPrincipal = Depends(authorized),
    ) -> JSONResponse:
        """Return one owner-scoped run record by id."""
        del principal
        return JSONResponse(await _fetch_owner_run(run_id))

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
