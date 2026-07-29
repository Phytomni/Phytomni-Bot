# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application construction and route wiring.

The public :func:`api.app.create_app` facade stays in ``app.py`` for import
compatibility. This module owns the wiring state so the facade does not also
become a second route implementation. App-local functions are resolved lazily
through ``api.app`` at request time; tests and deployments can therefore keep
patching the established compatibility seams.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from google.protobuf import json_format
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..agents.review.agent import load_review_settlement_adapter
from ..config.defaults import ApiConfig, BriefGeneConfig
from ..config.settings import SensitiveConfig
from ..interop.cache import DiscoveryCache
from ..interop.capabilities import DiscoveryResult
from ..interop.registry import InteropRegistry, InteropRegistryError
from ..runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from ..runtime.conversation_context.store import (
    ConversationContextStore,
    StoredTurn,
)
from ..runtime.locale import message_for
from ..runtime.memory import (
    MemorySchemaError,
    MemoryStore,
    MemoryWrite,
    memory_policy_from_config,
)
from ..runtime.run_registry import RunFilter, RunRequestInfo
from ..runtime.stage_trace import (
    current_stage_trace,
    stage_failure_from_exception,
)
from . import run_lifecycle
from .a2a.executor import A2AHandlerOptions, A2ARequestHandler
from .admin_auth import require_service_principal
from .app_support import _SAFE_DEFAULT_MESSAGES, _ErrorResponseOptions
from .auth import ApiPrincipal, require_principal, scopes_satisfy
from .lifecycle_contract import (
    LifecycleInvariantError,
    SafeApiError,
    SafeErrorCode,
    canonicalize_agent_run_body,
    canonicalize_run_record,
    run_persistence_error,
)
from .openai_mapping import (
    MODEL_TO_TOOL,
    flatten_messages,
    to_chat_completion,
    tool_accepts_obs,
    tool_for_model,
)
from .routes import admin as admin_routes
from .routes import agents as agent_routes
from .routes import conversation_context as conversation_context_routes
from .routes import memory as memory_routes
from .routes import runs as run_routes
from .schemas import (
    A2uiActionRequest,
    ChatCompletionRequest,
    ExpertQueryRequest,
    MemoryAuditRecordResponse,
    MemoryResponse,
    ResumeRequest,
)

logger = logging.getLogger(__name__)


def project_data_stage_error(exc: BaseException) -> SafeApiError | None:
    """Project the first failed DataAgent stage into a safe API error."""
    failure = stage_failure_from_exception(exc)
    if failure is not None:
        stage, error_code, final_http_status = failure
        status_code = final_http_status or 500
    else:
        event = next(
            (
                candidate
                for candidate in current_stage_trace()
                if candidate.error_code is not None
            ),
            None,
        )
        if event is None:
            return None
        stage = event.stage
        error_code = event.error_code or "internal_invariant_failed"
        status_code = event.final_http_status or 500
    message = {
        400: "invalid request",
        502: "upstream service failed",
        503: "service unavailable",
        504: "upstream service timed out",
    }.get(status_code, "internal server error")
    return SafeApiError(
        status_code=status_code,
        code=error_code,
        message=message,
        stage=stage,
        retryable=status_code in {502, 503, 504},
    )


def _safe_api_error_for_lifecycle(
    exc: LifecycleInvariantError,
) -> SafeApiError:
    """Map one internal lifecycle invariant failure to a safe HTTP error."""
    if exc.code is SafeErrorCode.SUCCEEDED_WITHOUT_PERSISTENCE:
        return run_persistence_error()
    if exc.code is SafeErrorCode.INPUT_REQUIRED_WITHOUT_SURFACE:
        return SafeApiError(
            status_code=500,
            code=exc.code.value,
            message="input required response is invalid",
            stage="projection",
        )
    if exc.code is SafeErrorCode.PROJECTION_FAILED:
        return SafeApiError(
            status_code=500,
            code=exc.code.value,
            message="result projection failed",
            stage="projection",
        )
    return SafeApiError(
        status_code=500,
        code=exc.code.value,
        message="agent run response violated lifecycle contract",
        stage="lifecycle",
    )


def _app_module() -> Any:
    """Load the public app module only after its import has completed."""
    return import_module(".app", package=__package__)


def _app_attr(name: str) -> Any:
    """Resolve one compatibility seam from the public app module."""
    return getattr(_app_module(), name)


def _api_config() -> ApiConfig:
    """Build the current API config through the public app seam."""
    config_type = _app_attr("ApiConfig")
    return config_type()


@dataclass
class _RuntimeState:
    """Mutable process state shared by lazily registered routes."""

    rate_limit: Callable[[str, int], int | None]
    memory_store: MemoryStore | None = None
    conversation_context_store: ConversationContextStore | None = None
    interop_registry: InteropRegistry | None = None
    interop_sensitive_config: SensitiveConfig | None = None
    interop_caches: dict[str, DiscoveryCache] | None = None

    def __post_init__(self) -> None:
        if self.interop_caches is None:
            self.interop_caches = {}

    def get_memory_store(self) -> MemoryStore:
        """Open the enabled local memory store on first use."""
        if self.memory_store is None:
            config = _api_config()
            try:
                self.memory_store = MemoryStore(
                    config.MEMORY_DB_PATH,
                    policy=memory_policy_from_config(config),
                )
            except (
                MemorySchemaError,
                OSError,
                sqlite3.Error,
                ValueError,
            ):
                _app_attr("_LOGGER").warning("memory store unavailable")
                raise HTTPException(
                    status_code=503, detail="memory store unavailable"
                ) from None
        return self.memory_store

    def get_conversation_context_store(self) -> ConversationContextStore:
        """Open the Bot-owned context store only for an enabled mutation."""
        if self.conversation_context_store is None:
            self.conversation_context_store = ConversationContextStore(
                _api_config().API_TASKS_DB_PATH
            )
        return self.conversation_context_store

    async def authorized(
        self,
        principal: ApiPrincipal = Depends(require_principal),
    ) -> ApiPrincipal:
        """Authenticate the caller and enforce the per-key request budget."""
        limit = _api_config().API_RATE_LIMIT_PER_MIN
        retry_after = self.rate_limit(principal.key_prefix, limit)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return principal

    def require_scope(
        self,
        *needed: str,
    ) -> Callable[..., Awaitable[ApiPrincipal]]:
        """Build a dependency requiring the caller to hold ``needed``."""

        async def _scoped(
            caller: ApiPrincipal = Depends(self.authorized),
        ) -> ApiPrincipal:
            if not scopes_satisfy(caller.scopes, needed):
                raise HTTPException(
                    status_code=403, detail="insufficient scope"
                )
            return caller

        return _scoped


@dataclass(frozen=True)
class _RouteAdapters:
    """Request-time adapters preserving the app-level patch seams."""

    runtime: _RuntimeState

    def memory_write(self, owner: str, payload: Any) -> MemoryWrite:
        """Project a memory write through the app-level helper."""
        return _app_attr("_memory_write")(owner, payload)

    def memory_response(self, record: Any) -> MemoryResponse:
        """Project a stored memory row through the app-level helper."""
        return _app_attr("_memory_response")(record)

    def memory_audit_response(self, record: Any) -> MemoryAuditRecordResponse:
        """Project an audit row through the app-level helper."""
        return _app_attr("_memory_audit_response")(record)

    def memory_revision(
        self,
        value: str | None,
        *,
        required: bool,
    ) -> int | None:
        """Parse the optimistic-concurrency revision header."""
        return _app_attr("_memory_revision")(value, required=required)

    def audit_record_to_dict(
        self,
        record: Any,
        config: ApiConfig,
    ) -> dict[str, Any]:
        """Project one relay-audit row through the app-level helper."""
        return _app_attr("_relay_audit_record_to_dict")(record, config)

    async def reconcile_task_logs(
        self,
        run_id: str,
        debug: bool,
    ) -> dict[str, Any]:
        """Reconcile task logs through the app-level compatibility seam."""
        return await _app_attr("_reconcile_run_task_logs")(run_id, debug)

    async def fetch_owner_run(
        self,
        run_id: str,
        *,
        debug: bool = False,
    ) -> dict[str, Any]:
        """Fetch one owner-scoped run through the app-level seam."""
        record = await _app_attr("_fetch_owner_run")(run_id, debug=debug)
        if isinstance(record, Mapping):
            return canonicalize_run_record(record, debug=debug)
        return record

    def list_owner_runs(
        self,
        request: run_routes.RunListRequest,
    ) -> dict[str, Any]:
        """Build the lifecycle query from one typed listing request."""
        query = run_lifecycle.RunListQuery(
            run_filter=RunFilter(
                status=request.filters.status,
                agent=request.filters.agent,
                origin=request.filters.origin,
                dialogue_id=request.filters.dialogue_id,
                created_after=request.paging.created_after,
                created_before=request.paging.created_before,
            ),
            limit=request.paging.limit,
            offset=request.paging.offset,
        )
        response = _app_attr("_list_owner_runs")(
            owner=request.owner,
            query=query,
            debug=request.paging.debug,
        )
        data = response.get("data")
        if not isinstance(data, list):
            return response
        return {
            **response,
            "data": [
                (
                    canonicalize_run_record(item, debug=request.paging.debug)
                    if isinstance(item, Mapping)
                    else item
                )
                for item in data
            ],
        }

    def strip_run_result(self, record: dict[str, Any]) -> dict[str, Any]:
        """Strip private result fields through the app-level helper."""
        return _app_attr("_strip_run_result")(record)

    async def resume_a2ui(
        self,
        *,
        run_id: str,
        body: A2uiActionRequest,
        debug: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Resume a paused A2UI run through the app-level seam."""
        response_body, status_code = await _app_attr("_resume_a2ui_run")(
            run_id=run_id,
            body=body,
            debug=debug,
        )
        return canonicalize_agent_run_body(response_body), status_code

    async def resume_review(
        self,
        *,
        thread_id: str,
        payload: ResumeRequest,
        debug: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Resume a paused Review run through the app-level seam."""
        response_body, status_code = await _app_attr("_resume_review_run")(
            thread_id=thread_id,
            payload=payload,
            debug=debug,
        )
        return canonicalize_agent_run_body(response_body), status_code

    def a2ui_enabled(self) -> bool:
        """Read the current A2UI feature flag."""
        return _api_config().A2UI_ENABLED

    def conversation_context_enabled(self) -> bool:
        """Read the current conversation-context protocol flag."""
        return _api_config().CONVERSATION_CONTEXT_V1_ENABLED

    def a2ui_max_response_bytes(self) -> int:
        """Read the configured A2UI response-size cap."""
        return _api_config().A2UI_MAX_RESPONSE_BYTES

    async def invoke_tool_enveloped(
        self,
        name: Any,
        arguments: dict[str, Any],
        *,
        conversation_messages: tuple[dict[str, str], ...] = (),
        agent_thread_id: str | None = None,
        private_agent_state: Mapping[str, Any] | None = None,
    ) -> Any:
        """Invoke one tool through the request-time app compatibility seam."""
        return await _app_attr("invoke_tool_enveloped")(
            name,
            arguments,
            conversation_messages=conversation_messages,
            agent_thread_id=agent_thread_id,
            private_agent_state=private_agent_state,
        )

    async def invoke_agent_run(
        self,
        *,
        agent: str,
        arguments: dict[str, Any],
        conversation_messages: tuple[dict[str, str], ...] = (),
        agent_thread_id: str | None = None,
        private_agent_state: Mapping[str, Any] | None = None,
        dialogue_id: str | None = None,
        request_json: str | None = None,
        debug: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Invoke a native agent run through the app-level seam."""
        response_body, status_code = await _app_attr("_invoke_agent_run")(
            agent=agent,
            arguments=arguments,
            conversation_messages=conversation_messages,
            agent_thread_id=agent_thread_id,
            private_agent_state=private_agent_state,
            dialogue_id=dialogue_id,
            request_json=request_json,
            debug=debug,
        )
        return canonicalize_agent_run_body(response_body), status_code

    async def expert_query(
        self,
        payload: ExpertQueryRequest,
        *,
        debug: bool,
    ) -> tuple[dict[str, Any], int]:
        """Route an Expert query through the app-level seam."""
        response_body, status_code = await _app_attr("_route_expert_query")(
            payload, debug=debug
        )
        return canonicalize_agent_run_body(response_body), status_code

    async def stream_chat_completion(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        payload: ChatCompletionRequest,
        user_query: str,
    ) -> Response:
        """Stream a chat completion through the app-level seam."""
        return await _app_attr("_stream_chat_response")(
            tool_name=tool_name,
            arguments=arguments,
            payload=payload,
            user_query=user_query,
        )

    async def review_chat_completion(
        self,
        *,
        payload: ChatCompletionRequest,
        arguments: Mapping[str, object],
        user_query: str,
    ) -> Response:
        """Stream a Review completion through the app-level seam."""
        return await _app_attr("_review_chat_completion_response")(
            payload=payload,
            arguments=arguments,
            user_query=user_query,
        )

    async def resolve_chat_query(
        self,
        *,
        raw_query: str,
        resolve_flag: bool,
        tool_name: str | None,
        brief_gene_resolver: Callable[..., Awaitable[Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a chat query through the app-level seam."""
        return await _app_attr("resolve_chat_query")(
            raw_query=raw_query,
            resolve_flag=resolve_flag,
            tool_name=tool_name,
            brief_gene_resolver=brief_gene_resolver,
        )

    async def brief_gene_resolver(
        self,
        raw_query: str,
        *,
        brief_config: BriefGeneConfig,
        sensitive_config: SensitiveConfig,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Resolve a BriefGene query while preserving legacy call shapes."""
        resolver = _app_attr("resolve_brief_gene_user_query")
        if timeout_seconds is None:
            return await resolver(
                raw_query,
                brief_config=brief_config,
                sensitive_config=sensitive_config,
            )
        return await resolver(
            raw_query,
            brief_config=brief_config,
            sensitive_config=sensitive_config,
            timeout_seconds=timeout_seconds,
        )

    def record_sync_run(
        self,
        *,
        agent: str,
        owner: str,
        result: dict[str, Any],
        request_info: RunRequestInfo | None = None,
    ) -> str | None:
        """Record a synchronous run through the app-level seam."""
        return _app_attr("_record_sync_run")(
            agent=agent,
            owner=owner,
            result=result,
            request_info=request_info,
        )


def _build_base_app() -> FastAPI:
    """Create the FastAPI object and install request context middleware."""
    _app_attr("configure_logging")()
    app = FastAPI(
        title="Phytomni HTTP API",
        version=_app_attr("__version__"),
        lifespan=_app_attr("_http_lifespan"),
    )
    app.add_middleware(
        _app_attr("request_context_middleware"),
    )
    return app


def _build_agent_dependencies(
    runtime: _RuntimeState,
    adapters: _RouteAdapters,
    context_executor: ConversationContextExecutor,
) -> agent_routes.AgentRouteDependencies:
    """Assemble the typed dependency graph for primary agent routes."""
    return agent_routes.AgentRouteDependencies(
        auth=agent_routes.AgentAuthDependencies(
            require_agents=runtime.require_scope("agents"),
            schedule_run_gc=_app_attr("_schedule_run_gc"),
        ),
        catalog=agent_routes.AgentCatalogDependencies(
            model_to_tool=MODEL_TO_TOOL,
            model_to_agent_slug=_app_attr("_MODEL_TO_AGENT_SLUG"),
            agent_slug_to_tool=_app_attr("_AGENT_SLUG_TO_TOOL"),
            remote_agent_slugs=_app_attr("_REMOTE_AGENT_SLUGS"),
            legacy_aliases=_app_attr("_LEGACY_ALIASES"),
            serialize_capability=_app_attr("serialize_agent_capability"),
            conversation_context_enabled=adapters.conversation_context_enabled,
        ),
        chat=agent_routes.AgentChatDependencies(
            input=agent_routes.AgentChatInputDependencies(
                tool_for_model=tool_for_model,
                tool_accepts_obs=tool_accepts_obs,
                flatten_messages=flatten_messages,
                resolve_chat_query=adapters.resolve_chat_query,
                brief_gene_resolver=adapters.brief_gene_resolver,
            ),
            execution=agent_routes.AgentChatExecutionDependencies(
                invoke_tool_enveloped=adapters.invoke_tool_enveloped,
                stream_chat_completion=adapters.stream_chat_completion,
                review_chat_completion=adapters.review_chat_completion,
            ),
            projection=agent_routes.AgentChatProjectionDependencies(
                record_sync_run=adapters.record_sync_run,
                current_user=_app_attr("current_request_user"),
                to_chat_completion=to_chat_completion,
                strip_chat_completion=_app_attr("strip_chat_completion"),
                resolve_debug=_app_attr("resolve_debug"),
            ),
        ),
        native=agent_routes.AgentNativeDependencies(
            invoke_agent_run=adapters.invoke_agent_run,
            route_expert_query=adapters.expert_query,
        ),
        context=agent_routes.AgentContextDependencies(
            enabled=adapters.conversation_context_enabled,
            executor=context_executor,
        ),
        upload=agent_routes.AgentUploadDependencies(
            handle_file_upload=_app_attr("handle_file_upload"),
            error_response=_app_attr("_error_response"),
        ),
    )


def _register_interop_route(
    app: FastAPI,
    runtime: _RuntimeState,
    require_scope: Callable[..., Any],
) -> None:
    """Register metadata-only interop discovery when enabled."""
    if not _api_config().INTEROP_ENABLED:
        return

    @app.get("/v1/interop/capabilities")
    async def list_interop_capabilities(
        request: Request,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """List sanitized metadata for operator-approved targets."""
        del principal
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="interop capabilities accepts no query parameters",
            )
        registry = runtime.interop_registry
        sensitive_config = runtime.interop_sensitive_config
        if registry is None:
            try:
                sensitive_config = SensitiveConfig.load()
                registry = _app_attr("load_interop_registry")(
                    _api_config(), sensitive_config
                )
                runtime.interop_sensitive_config = sensitive_config
                runtime.interop_registry = registry
            except (
                InteropRegistryError,
                OSError,
                RuntimeError,
                TypeError,
                ValidationError,
                ValueError,
            ) as exc:
                logger.warning(
                    "interop registry unavailable: %s",
                    exc.__class__.__name__,
                )
                return _app_attr("_error_response")(
                    503, "interop registry unavailable"
                )
        assert registry is not None
        if not registry.enabled:
            return _app_attr("_error_response")(
                404, "interop capabilities unavailable"
            )
        assert sensitive_config is not None
        result: DiscoveryResult = await _app_attr("_discover_interop_targets")(
            registry,
            sensitive_config=sensitive_config,
            caches=runtime.interop_caches,
        )
        return JSONResponse(_app_attr("_interop_result_body")(result))


def _register_health_routes(app: FastAPI) -> None:
    """Register unauthenticated liveness and readiness probes."""

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Return a dependency-free liveness signal."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Return readiness after checking local store paths."""
        config = _api_config()
        checks = {
            "api_keys_db": _app_attr("_store_path_writable")(
                config.API_KEYS_DB_PATH
            ),
            "tasks_db": _app_attr("_store_path_writable")(
                config.API_TASKS_DB_PATH
            ),
        }
        if not all(checks.values()):
            return _app_attr("_error_response")(
                503, "one or more local stores are not writable"
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "checks": checks},
        )


def _register_memory_and_admin_routes(
    app: FastAPI,
    runtime: _RuntimeState,
    adapters: _RouteAdapters,
) -> None:
    """Register memory and service-admin routes behind their feature gates."""
    if _api_config().MEMORY_ENABLED:
        memory_agents = runtime.require_scope("agents")
        memory_routes.register_memory_routes(
            app,
            memory_routes.MemoryRouteDependencies(
                get_store=runtime.get_memory_store,
                auth=memory_routes.MemoryAuthDependencies(
                    require_agents=memory_agents,
                    require_service=require_service_principal,
                ),
                context=memory_routes.MemoryContextDependencies(
                    current_user=_app_attr("current_request_user"),
                    current_request_id=_app_attr("current_request_id"),
                ),
                projection=memory_routes.MemoryProjectionDependencies(
                    memory_write=adapters.memory_write,
                    memory_response=adapters.memory_response,
                    memory_audit_response=adapters.memory_audit_response,
                    memory_revision=adapters.memory_revision,
                ),
            ),
        )
    admin_routes.register_admin_routes(
        app,
        admin_routes.AdminRouteDependencies(
            require_service=require_service_principal,
            audit_record_to_dict=adapters.audit_record_to_dict,
        ),
    )


def _register_run_routes(
    app: FastAPI,
    runtime: _RuntimeState,
    adapters: _RouteAdapters,
) -> None:
    """Register owner-scoped run history and pause/resume routes."""
    run_routes.register_run_routes(
        app,
        run_routes.RunRouteDependencies(
            auth=run_routes.RunAuthDependencies(
                require_agents=runtime.require_scope("agents")
            ),
            context=run_routes.RunContextDependencies(
                current_user=_app_attr("current_request_user"),
                service_token_valid=_app_attr("is_service_token_valid"),
            ),
            projection=run_routes.RunProjectionDependencies(
                reconcile_task_logs=adapters.reconcile_task_logs,
                fetch_owner_run=adapters.fetch_owner_run,
                list_owner_runs=adapters.list_owner_runs,
                strip_run_result=adapters.strip_run_result,
            ),
            pause=run_routes.RunPauseDependencies(
                a2ui_enabled=adapters.a2ui_enabled,
                a2ui_max_response_bytes=adapters.a2ui_max_response_bytes,
                resume_a2ui=adapters.resume_a2ui,
                resume_review=adapters.resume_review,
            ),
        ),
    )


def _register_conversation_context_routes(
    app: FastAPI,
    runtime: _RuntimeState,
    adapters: _RouteAdapters,
    context_executor: ConversationContextExecutor,
) -> None:
    """Register authenticated V1 context mutation routes."""
    if not adapters.conversation_context_enabled():
        return
    conversation_context_routes.register_conversation_context_routes(
        app,
        conversation_context_routes.ContextRouteDependencies(
            enabled=adapters.conversation_context_enabled,
            require_agents=runtime.require_scope("agents"),
            get_store=runtime.get_conversation_context_store,
            acknowledge_review_settlement=(
                context_executor.acknowledge_review_settlement_for_turn
            ),
        ),
    )


def _register_a2a_routes(
    app: FastAPI,
    require_scope: Callable[..., Any],
) -> None:
    """Register the optional A2A JSON-RPC and card routes."""
    config = _api_config()
    if not config.A2A_ENABLED:
        return
    public_base_url = config.A2A_PUBLIC_BASE_URL
    assert public_base_url is not None
    handler = A2ARequestHandler(
        invoke_agent_run=_app_attr("_invoke_agent_run"),
        options=A2AHandlerOptions(
            invoke_agent_stream=_app_attr("invoke_tool_streamed"),
            record_a2a=_app_attr("_record_a2a_registration"),
            get_a2a_task=_app_attr("_get_a2a_task"),
            resume_a2a=_app_attr("_resume_a2a_task"),
        ),
        tool_to_agent={
            tool_name: agent
            for agent, tool_name in _app_attr("_AGENT_SLUG_TO_TOOL").items()
        },
        select_agent=_app_attr("select_agent_tool"),
    )
    a2a_route = create_jsonrpc_routes(handler, "/a2a")[0]

    @app.get("/.well-known/agent-card.json")
    async def a2a_agent_card() -> JSONResponse:
        """Return the public A2A card when the feature is enabled."""
        card = _app_attr("build_agent_card")(public_base_url)
        return JSONResponse(json_format.MessageToDict(card))

    @app.post("/a2a")
    async def a2a_jsonrpc(
        request: Request,
        a2a_version: str | None = Header(default=None, alias="A2A-Version"),
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> Response:
        """Authenticate and dispatch one A2A v1 JSON-RPC request."""
        del principal
        if a2a_version != "1.0":
            return _app_attr("_error_response")(
                400,
                "A2A-Version must be exactly 1.0",
            )
        return await a2a_route.endpoint(request)


def _register_error_handlers(app: FastAPI) -> None:
    """Install the unified HTTP error envelope handlers."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        """Render HTTP exceptions through the unified envelope."""
        return _app_attr("_error_response")(
            exc.status_code,
            _SAFE_DEFAULT_MESSAGES.get(exc.status_code, "request failed"),
            options=_ErrorResponseOptions(
                headers=getattr(exc, "headers", None)
            ),
        )

    @app.exception_handler(SafeApiError)
    async def safe_api_error_handler(
        _request: Request,
        exc: SafeApiError,
    ) -> JSONResponse:
        """Render typed public-safe API errors through the unified envelope."""
        return _app_attr("_error_response")(
            exc.status_code,
            exc.message,
            options=_ErrorResponseOptions(
                code=exc.code,
                stage=exc.stage,
                retryable=exc.retryable,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Render request validation errors as 422 envelopes."""
        if any(
            "locale" in {str(part) for part in error.get("loc", ())}
            for error in exc.errors()
        ):
            return _app_attr("_error_response")(
                422,
                message_for("unsupported_locale", "en-US"),
                options=_ErrorResponseOptions(
                    code="unsupported_locale",
                    stage="request_validation",
                    retryable=False,
                ),
            )
        return _app_attr("_error_response")(422, "request validation failed")

    @app.exception_handler(LifecycleInvariantError)
    async def lifecycle_exception_handler(
        _request: Request,
        exc: LifecycleInvariantError,
    ) -> JSONResponse:
        """Render lifecycle violations as safe internal error envelopes."""
        safe_error = _safe_api_error_for_lifecycle(exc)
        return _app_attr("_error_response")(
            safe_error.status_code,
            safe_error.message,
            options=_ErrorResponseOptions(
                code=safe_error.code,
                stage=safe_error.stage,
                retryable=safe_error.retryable,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        """Render unexpected errors as 500 envelopes."""
        failed_events = tuple(
            event
            for event in current_stage_trace()
            if event.error_code is not None
        )
        if not failed_events:
            return _app_attr("_error_response")(500, "internal server error")
        event = failed_events[0]
        status_code = event.final_http_status or 500
        return _app_attr("_error_response")(
            status_code,
            _SAFE_DEFAULT_MESSAGES.get(status_code, "internal server error"),
            options=_ErrorResponseOptions(
                code=event.error_code,
                stage=event.stage,
                retryable=status_code in {502, 503, 504},
            ),
        )


def _build_context_executor(
    runtime: _RuntimeState,
) -> ConversationContextExecutor:
    """Build the lazy, Bot-owned conversation context executor."""

    async def select_agent(*args: Any, **kwargs: Any) -> Any:
        """Resolve the selector lazily so established test seams
        remain live.
        """
        return await _app_attr("select_agent_tool")(*args, **kwargs)

    async def load_review_settlement(
        metadata: Mapping[str, Any], staged_turn: StoredTurn
    ) -> Any:
        """Rebuild Review promotion state through its private agent seam."""
        return await load_review_settlement_adapter(metadata, staged_turn)

    return ConversationContextExecutor(
        store_factory=runtime.get_conversation_context_store,
        select_agent=select_agent,
        api_config_factory=_api_config,
        review_settlement_loader=load_review_settlement,
    )


def build_app(
    *, context_executor: ConversationContextExecutor | None = None
) -> FastAPI:
    """Build the complete FastAPI application from typed route seams."""
    app = _build_base_app()
    runtime = _RuntimeState(rate_limit=_app_attr("make_rate_limiter")())
    adapters = _RouteAdapters(runtime)
    context_executor = context_executor or _build_context_executor(runtime)
    agent_dependencies = _build_agent_dependencies(
        runtime,
        adapters,
        context_executor,
    )

    _register_interop_route(app, runtime, runtime.require_scope)
    _register_health_routes(app)
    agent_routes.register_model_route(app, agent_dependencies)
    _register_memory_and_admin_routes(app, runtime, adapters)
    agent_routes.register_agent_routes(app, agent_dependencies)
    _register_conversation_context_routes(
        app, runtime, adapters, context_executor
    )
    _register_run_routes(app, runtime, adapters)
    _register_a2a_routes(app, runtime.require_scope)
    app.include_router(_app_attr("create_relay_router")())
    _register_error_handlers(app)
    return app


__all__ = ["build_app"]
