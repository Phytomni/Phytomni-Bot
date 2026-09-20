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
import json
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..agents.research.recovery import recover_registered_startup
from ..agents.shared.citation_database import validate_citation_database
from ..agents.shared.gauss import aclose_gauss_pool
from ..config.defaults import ApiConfig
from ..config.settings import SensitiveConfig
from ..interop.cache import DiscoveryCache, get_or_create_discovery_cache
from ..interop.capabilities import DiscoveryError, DiscoveryResult
from ..interop.models import InteropTarget
from ..interop.registry import InteropRegistry
from ..mcp.result_formatting import strip_agent_result
from ..runtime.execution_command_dispatcher_v2 import (
    run_execution_command_dispatcher,
)
from ..runtime.execution_command_reconciler_v2 import (
    run_execution_command_reconciler,
)
from ..runtime.execution_entrypoint_v2 import bind_routed_reservation_identity
from ..runtime.execution_supervisor_service_v2 import (
    run_execution_supervisor_service,
)
from ..runtime.execution_supervisor_v2 import run_auxiliary_recovery
from ..runtime.locale import (
    SupportedLocale,
    UnsupportedLocaleError,
    bind_effective_locale,
    current_effective_locale,
    message_for,
    resolve_effective_locale,
)
from ..runtime.memory import MemoryWrite
from ..runtime.outbound import (
    aclose_outbound_runtime,
    init_outbound_runtime,
)
from ..runtime.request_context import (
    bind_accepted_task_ids,
    bind_pre_recorded_task_id,
    bind_recorder_degraded,
    bind_request_id,
    bind_request_user,
    bind_run_id,
    current_request_id,
    current_request_user,
    reset_request_var,
)
from ..runtime.run_registry import RunRequestInfo
from ..runtime.stage_trace import bind_stage_trace
from ..runtime.task_manager import resolve_tasks_db_path
from ..storage.path_policy import IdFactory
from . import run_lifecycle
from .execution_dispatch import (
    ExecutionDispatchServices,
    build_execution_command_invoker,
    execution_conversation_dispatch_kind,
)
from .execution_dispatch import (
    execution_conversation_runtime as _execution_conversation_runtime,
)
from .lifecycle_contract import SafeApiError
from .research_capabilities import refresh_research_relay_capability
from .research_input import ensure_research_input_runtime
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

_SAFE_DEFAULT_MESSAGES = {
    400: "invalid request",
    401: "authentication required",
    403: "request is not permitted",
    404: "resource not found",
    409: "request conflicts with current state",
    413: "request payload is too large",
    422: "request validation failed",
    429: "request rate limit exceeded",
    500: "internal server error",
    502: "upstream service failed",
    503: "service unavailable",
    504: "upstream service timed out",
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
    code = options.code or _DEFAULT_ERROR_CODES.get(status_code, "error")
    public_message = message
    locale = current_effective_locale()
    if locale == "zh-CN":
        is_fixed_default = message == _SAFE_DEFAULT_MESSAGES.get(status_code)
        if options.code is not None or is_fixed_default:
            try:
                public_message = message_for(code, locale)
            except KeyError:
                # Unknown domain codes keep their already-sanitized message.
                public_message = message
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            code=code,
            message=public_message,
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


def safe_chat_completion_request_json(
    *,
    model: str,
    dialogue_id: str | None,
    locale: SupportedLocale,
    stream: bool,
) -> str:
    """Serialize only bounded chat-completion request metadata."""
    return json.dumps(
        {
            "model": model,
            "dialogue_id": dialogue_id,
            "locale": locale,
            "stream": stream,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def build_safe_chat_request_info(
    payload: Any,
    user_query: str,
    *,
    tool_name: str,
    locale: SupportedLocale | None = None,
) -> RunRequestInfo:
    """Build one bounded chat/review run-registry request record."""
    effective_locale = current_effective_locale() if locale is None else locale
    return RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name=tool_name,
        model=payload.model,
        request_json=safe_chat_completion_request_json(
            model=payload.model,
            dialogue_id=payload.dialogue_id,
            locale=effective_locale,
            stream=bool(payload.stream),
        ),
        locale=effective_locale,
    )


def resolve_http_locale(
    *,
    explicit: str | None,
    accept_language: str | None,
    latest_user_query: str,
) -> SupportedLocale:
    """Resolve and bind the effective locale for one HTTP request."""
    try:
        locale = resolve_effective_locale(
            explicit=explicit,
            accept_language=accept_language,
            latest_user_query=latest_user_query,
        )
    except UnsupportedLocaleError as exc:
        raise SafeApiError(
            status_code=422,
            code="unsupported_locale",
            message=message_for("unsupported_locale", "en-US"),
            stage="request_validation",
            retryable=False,
        ) from exc
    bind_effective_locale(locale)
    return locale


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
        accepted_task_ids_token = bind_accepted_task_ids(())
        degraded_token = bind_recorder_degraded(False)
        locale_token = bind_effective_locale("en-US")
        stage_trace_token = bind_stage_trace()

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
            reset_request_var(stage_trace_token)
            reset_request_var(degraded_token)
            reset_request_var(accepted_task_ids_token)
            reset_request_var(pre_recorded_token)
            reset_request_var(run_token)
            reset_request_var(user_token)
            reset_request_var(id_token)
            reset_request_var(locale_token)

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


def bind_research_input_state(
    app: FastAPI, root_request_factory: Any, runtime: Any
) -> None:
    """Bind Research root and managed-asset factories for lifespan startup."""
    app.state.research_input_root_request_factory = root_request_factory
    app.state.research_input_asset_resolver_factory = (
        runtime.upload_runtime.get_asset_resolver
    )


@asynccontextmanager
async def _http_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Own the process-wide outbound runtime and Gauss clients."""
    validate_citation_database()
    await init_outbound_runtime()
    try:
        await refresh_research_relay_capability(ApiConfig())
        state = getattr(_app, "state", None)
        root_request_factory = getattr(
            state, "research_input_root_request_factory", None
        )
        asset_resolver_factory = getattr(
            state, "research_input_asset_resolver_factory", None
        )
        if root_request_factory is None and asset_resolver_factory is None:
            ensure_research_input_runtime()
        else:
            ensure_research_input_runtime(
                root_request_factory=root_request_factory,
                asset_resolver_factory=asset_resolver_factory,
            )
        await run_auxiliary_recovery(
            name="research",
            recover=recover_registered_startup,
        )
        dispatcher_stop = asyncio.Event()
        reconciler_stop = asyncio.Event()
        supervisor_stop = asyncio.Event()
        dispatch_invoke = build_execution_command_invoker(
            ExecutionDispatchServices(
                app=_app,
                app_attr=_app_attr,
                current_user=current_request_user,
                tasks_db_path=resolve_tasks_db_path,
                routed_reservation_binder=bind_routed_reservation_identity,
            )
        )

        dispatcher = asyncio.create_task(
            run_execution_command_dispatcher(
                db_path=str(resolve_tasks_db_path()),
                invoke=dispatch_invoke,
                stop=dispatcher_stop,
            ),
            name="execution-command-dispatcher-v2",
        )
        reconciler = asyncio.create_task(
            run_execution_command_reconciler(
                db_path=str(resolve_tasks_db_path()),
                stop=reconciler_stop,
            ),
            name="execution-command-reconciler-v2",
        )
        supervisor = asyncio.create_task(
            run_execution_supervisor_service(
                db_path=str(resolve_tasks_db_path()),
                stop=supervisor_stop,
            ),
            name="execution-supervisor-v2",
        )
        try:
            yield
        finally:
            dispatcher_stop.set()
            reconciler_stop.set()
            supervisor_stop.set()
            try:
                await asyncio.wait_for(dispatcher, timeout=5.0)
            except TimeoutError:
                dispatcher.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatcher
            try:
                await asyncio.wait_for(reconciler, timeout=5.0)
            except TimeoutError:
                reconciler.cancel()
                with suppress(asyncio.CancelledError):
                    await reconciler
            try:
                await asyncio.wait_for(supervisor, timeout=5.0)
            except TimeoutError:
                supervisor.cancel()
                with suppress(asyncio.CancelledError):
                    await supervisor
    finally:
        await aclose_outbound_runtime()
        await aclose_gauss_pool()


__all__ = [
    "_discover_interop_target",
    "_execution_conversation_runtime",
    "_nearest_existing",
    "discover_interop_targets",
    "error_response",
    "execution_conversation_dispatch_kind",
    "_http_lifespan",
    "interop_result_body",
    "memory_audit_response",
    "memory_response",
    "memory_revision",
    "memory_write",
    "reconcile_run_task_logs",
    "request_context_middleware",
    "build_safe_chat_request_info",
    "resolve_http_locale",
    "safe_chat_completion_request_json",
    "store_path_writable",
    "stream_answer_max_bytes",
]
