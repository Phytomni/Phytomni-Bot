# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config.defaults import ApiConfig
from ..mcp.app import invoke_tool_raw
from ..runtime.request_context import (
    bind_request_id,
    current_request_id,
    reset_request_var,
)
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
from .schemas import ApiErrorDetail, ApiErrorResponse, ChatCompletionRequest

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
    error envelope and clients can correlate a call. A closure-based pure
    ASGI middleware is used (not BaseHTTPMiddleware) so the contextvar is
    set in the same task that runs the endpoint and exception handlers.

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
        token = bind_request_id(request_id)

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
            reset_request_var(token)

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
            "api_runs_db": _store_path_writable(config.API_RUNS_DB_PATH),
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
        result = await invoke_tool_raw(tool_name, arguments)
        return JSONResponse(to_chat_completion(result, payload.model))

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
