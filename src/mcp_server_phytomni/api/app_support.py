# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small, typed support seams used by the FastAPI application factory.

The public ``api.app`` module keeps compatibility aliases for these helpers,
while this module owns request context, health/readiness, memory projection,
interop discovery, and process-lifetime plumbing.  The indirection back to
``api.app`` is deliberate: existing tests and integrations patch the app-level
interop clients and run lookup seams, so moving the implementation must not
silently bypass those patches.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..agents.shared.gauss import aclose_gauss_pool
from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..config.defaults import ApiConfig
from ..config.settings import SensitiveConfig
from ..interop.cache import DiscoveryCache, get_or_create_discovery_cache
from ..interop.capabilities import DiscoveryError, DiscoveryResult
from ..interop.models import InteropTarget
from ..interop.registry import InteropRegistry
from ..mcp.result_formatting import strip_agent_result
from ..runtime.memory import MemoryWrite
from ..runtime.request_context import (
    bind_pre_recorded_task_id,
    bind_request_id,
    bind_request_user,
    bind_run_id,
    current_request_id,
    reset_request_var,
)
from ..storage.path_policy import IdFactory
from . import run_lifecycle
from .schemas import (
    ApiErrorDetail,
    ApiErrorResponse,
    MemoryAuditRecordResponse,
    MemoryResponse,
)
from .stream_answer import resolve_stream_answer_max_bytes

_DEFAULT_ERROR_CODES = {
    400: "invalid_argument",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "run_state_conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_invariant_failed",
    502: "upstream_failed",
    503: "unavailable",
    504: "upstream_timeout",
}


@dataclass(frozen=True, slots=True)
class _ErrorResponseOptions:
    """Optional fields for the common public error envelope."""

    code: str | None = None
    stage: str | None = None
    retryable: bool = False
    headers: Mapping[str, str] | None = None


def _app_attr(name: str) -> Any:
    """Resolve a legacy app-level seam at call time."""
    module = import_module(".app", package=__package__)
    return getattr(module, name)


def error_response(
    status_code: int,
    message: str,
    *,
    options: _ErrorResponseOptions | None = None,
) -> JSONResponse:
    """Build a unified error-envelope JSON response."""
    options = options or _ErrorResponseOptions()
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            code=options.code
            or _DEFAULT_ERROR_CODES.get(status_code, "error"),
            message=message,
            request_id=current_request_id() or "unknown",
            stage=options.stage,
            retryable=options.retryable,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(exclude_none=True),
        headers=dict(options.headers or {}),
    )


def memory_response(record: Any) -> MemoryResponse:
    """Convert a domain memory record into the public response shape."""
    return MemoryResponse.model_validate(record.model_dump())


def memory_audit_response(record: Any) -> MemoryAuditRecordResponse:
    """Convert one digest-only audit record into its public shape."""
    return MemoryAuditRecordResponse.model_validate(record.model_dump())


def memory_write(owner: str, payload: Any) -> MemoryWrite:
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


def memory_revision(value: str | None, *, required: bool) -> int | None:
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
    """Wrap an ASGI app to bind a per-request correlation id."""

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        """Bind request context, inject the response id, then delegate."""
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
    """Return the closest existing ancestor of a path."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor or ".")


def store_path_writable(raw_path: str) -> bool:
    """Check whether a configured SQLite path's directory is writable."""
    parent = Path(raw_path).expanduser().resolve().parent
    return os.access(_nearest_existing(parent), os.W_OK)


async def _discover_interop_target(
    target: InteropTarget,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig,
    cache: DiscoveryCache,
) -> DiscoveryResult:
    """Discover one configured target through the app-level client seam."""
    if target.kind == "mcp":
        return await _app_attr("discover_external_mcp_capabilities")(
            target.id,
            registry=registry,
            sensitive_config=sensitive_config,
            cache=cache,
        )
    return await _app_attr("discover_external_a2a_capabilities")(
        target.id,
        registry=registry,
        sensitive_config=sensitive_config,
        cache=cache,
    )


async def discover_interop_targets(
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

    target_ids = registry.target_ids()
    results = await asyncio.gather(
        *(_one(target_id) for target_id in target_ids),
        return_exceptions=True,
    )
    data: list[Any] = []
    errors: list[DiscoveryError] = []
    for target_id, result in zip(target_ids, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            target = registry.require_target(target_id)
            _app_attr("_LOGGER").warning(
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


def interop_result_body(result: DiscoveryResult) -> dict[str, Any]:
    """Serialize only shared capability DTOs and safe error fields."""
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


async def reconcile_run_task_logs(run_id: str, debug: bool) -> dict[str, Any]:
    """Reconcile a run's task log through the owner-checked app seam."""
    return await run_lifecycle.reconcile_run_task_logs(
        run_id,
        debug,
        fetch=_app_attr("_fetch_owner_run"),
        reconcile=_app_attr("reconcile_task_log"),
        strip=strip_agent_result,
    )


def stream_answer_max_bytes() -> int:
    """Return the resolved soft cap for streamed chat answer storage."""
    return resolve_stream_answer_max_bytes(ApiConfig().STREAM_ANSWER_MAX_BYTES)


@asynccontextmanager
async def _http_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Own the process-wide shared HTTP and Gauss clients."""
    init_shared_client()
    try:
        yield
    finally:
        await aclose_shared_client()
        await aclose_gauss_pool()


__all__ = [
    "_discover_interop_target",
    "_nearest_existing",
    "discover_interop_targets",
    "error_response",
    "_http_lifespan",
    "interop_result_body",
    "memory_audit_response",
    "memory_response",
    "memory_revision",
    "memory_write",
    "reconcile_run_task_logs",
    "request_context_middleware",
    "store_path_writable",
    "stream_answer_max_bytes",
]
