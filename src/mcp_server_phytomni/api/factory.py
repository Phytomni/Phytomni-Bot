# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI construction and lazily wired routes.

``api.app.create_app`` stays in ``app.py``; this module preserves lazy
test and deployment seams.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property, partial
from importlib import import_module
from typing import Any, Unpack

from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse, Response
from google.protobuf import json_format
from pydantic import ValidationError

from ..agents.research.input_contracts import ResearchCoordinatorRequest
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
)
from ..runtime.memory import (
    MemorySchemaError,
    MemoryStore,
    MemoryWrite,
    memory_policy_from_config,
)
from ..runtime.run_registry import RunFilter, RunRegistry, RunRequestInfo
from . import agent_runs as _agent_runs
from . import app_support, run_lifecycle
from . import stage_errors as _stage_errors
from .a2a.executor import A2AHandlerOptions, A2ARequestHandler
from .admin_auth import require_service_principal
from .auth import (
    ApiPrincipal,
    require_explicit_scope,
    require_principal,
)
from .auth import (
    require_scope as build_scope_dependency,
)
from .context_executor_factory import build_context_executor
from .error_handlers import register_error_handlers
from .lifecycle_contract import (
    SafeApiError,
    canonicalize_agent_run_body,
    canonicalize_run_record,
)
from .openai_mapping import (
    MODEL_TO_TOOL,
    split_chat_messages,
    to_chat_completion,
    tool_accepts_obs,
    tool_for_model,
)
from .research_input import (
    ResearchAdmissionRequest,
    ResearchHttpAdmissionInput,
)
from .routes import admin as admin_routes
from .routes import agents as agent_routes
from .routes import conversation_context as conversation_context_routes
from .routes import memory as memory_routes
from .routes import runs as run_routes
from .schemas import (
    A2uiActionRequest,
    ChatCompletionRequest,
    ChatStreamCall,
    ExpertQueryRequest,
    MemoryAuditRecordResponse,
    MemoryResponse,
    ResumeRequest,
)
from .upload_runtime import (
    UploadRuntime,
    install_upload_cors,
)

__all__ = ["build_app"]
logger = logging.getLogger(__name__)


def project_data_stage_error(exc: BaseException) -> SafeApiError | None:
    """Preserve the legacy factory seam for DataAgent error projection."""
    return _stage_errors.project_data_stage_error(exc)


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


def _tasks_db_path() -> str:
    """Resolve the task database path through the live app seam."""
    return _app_attr("resolve_tasks_db_path")()


@dataclass
class _RuntimeState:
    """Mutable process state shared by lazily registered routes."""

    rate_limit: Callable[[str, int], int | None]
    memory_store: MemoryStore | None = None
    conversation_context_store: ConversationContextStore | None = None
    interop_registry: InteropRegistry | None = None
    interop_sensitive_config: SensitiveConfig | None = None
    interop_caches: dict[str, DiscoveryCache] = field(default_factory=dict)

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

    @cached_property
    def upload_runtime(self) -> UploadRuntime:
        """Build the process-local upload runtime on first access."""
        return UploadRuntime(_api_config, _app_attr("_LOGGER"))

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


@dataclass(frozen=True)
class _RouteAdapters:
    """Request-time adapters preserving the app-level patch seams."""

    runtime: _RuntimeState
    run_registry_factory: Callable[[str], Any] = RunRegistry
    research_input_runtime_required: bool = False

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
        record = await _app_attr("_fetch_owner_run")(
            run_id,
            debug=debug,
            registry_factory=self.run_registry_factory,
        )
        if isinstance(record, Mapping):
            return canonicalize_run_record(record, debug=debug)
        return record

    async def _retry_owner_delivery(self, run_id: str) -> dict[str, Any]:
        """Begin one archive retry through the app compatibility seam."""
        return await _app_attr("_retry_owner_delivery")(
            run_id, registry_factory=self.run_registry_factory
        )

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

    def _a2ui_enabled(self) -> bool:
        """Read the current A2UI feature flag."""
        return _api_config().A2UI_ENABLED

    def _conversation_context_enabled(self) -> bool:
        """Read the current conversation-context protocol flag."""
        return _api_config().CONVERSATION_CONTEXT_V1_ENABLED

    def a2ui_max_response_bytes(self) -> int:
        """Read the configured A2UI response-size cap."""
        return _api_config().A2UI_MAX_RESPONSE_BYTES

    async def invoke_tool_enveloped(
        self,
        name: Any,
        arguments: dict[str, Any],
        **options: Any,
    ) -> Any:
        """Invoke one tool through the request-time app compatibility seam."""
        conversation_messages = options.pop("conversation_messages", ())
        agent_thread_id = options.pop("agent_thread_id", None)
        private_agent_state = options.pop("private_agent_state", None)
        if options:
            raise TypeError(
                "unexpected tool invocation options: "
                + ", ".join(sorted(options))
            )
        return await _app_attr("invoke_tool_enveloped")(
            name=name,
            arguments=arguments,
            conversation_messages=conversation_messages,
            agent_thread_id=agent_thread_id,
            private_agent_state=private_agent_state,
        )

    async def invoke_agent_run(
        self,
        **options: Any,
    ) -> tuple[dict[str, Any], int]:
        """Invoke a native agent run through the app-level seam."""
        try:
            agent = options.pop("agent")
            arguments = options.pop("arguments")
        except KeyError as exc:
            raise TypeError(
                f"missing agent-run option: {exc.args[0]}"
            ) from exc
        research_http_input = options.pop("research_http_input", None)
        research_attachment_bundle = options.pop(
            "research_attachment_bundle", None
        )
        if research_http_input is not None:
            if not isinstance(research_http_input, ResearchHttpAdmissionInput):
                raise TypeError("invalid Research HTTP admission input")
            return await _agent_runs.invoke_research_http_run(
                research_http_input,
                research_attachment_bundle,
                config=_api_config(),
                db_path=_tasks_db_path(),
                runtime_options=_agent_runs.ResearchHttpRuntimeOptions(
                    allow_uninstalled=not self.research_input_runtime_required
                ),
            )
        response_body, status_code = await _app_attr("_invoke_agent_run")(
            agent=agent, arguments=arguments, **options
        )
        return canonicalize_agent_run_body(response_body), status_code

    async def expert_query(
        self,
        payload: ExpertQueryRequest,
        *,
        debug: bool,
        attachment_input: Any | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Route an Expert query through the app-level seam."""
        response_body, status_code = await _app_attr("_route_expert_query")(
            payload,
            debug=debug,
            attachment_input=attachment_input,
            idempotency_key=idempotency_key,
            research_runtime_options=_agent_runs.ResearchHttpRuntimeOptions(
                allow_uninstalled=not self.research_input_runtime_required
            ),
        )
        return canonicalize_agent_run_body(response_body), status_code

    async def stream_chat_completion(
        self,
        **request: Unpack[ChatStreamCall],
    ) -> Response:
        """Stream a chat completion through the app-level seam."""
        return await _app_attr("_stream_chat_response")(**request)

    async def review_chat_completion(
        self,
        *,
        payload: ChatCompletionRequest,
        arguments: Mapping[str, object],
        user_query: str,
        attachment_evidence: Any | None = None,
    ) -> Response:
        """Stream a Review completion through the app-level seam."""
        return await _app_attr("_review_chat_completion_response")(
            payload=payload,
            arguments=arguments,
            user_query=user_query,
            attachment_evidence=attachment_evidence,
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
    install_upload_cors(app, _api_config().API_UPLOAD_V2_ALLOWED_ORIGINS)
    return app


def _build_agent_dependencies(
    runtime: _RuntimeState,
    adapters: _RouteAdapters,
    context_executor: ConversationContextExecutor,
) -> agent_routes.AgentRouteDependencies:
    """Assemble the typed dependency graph for primary agent routes."""

    upload_runtime = runtime.upload_runtime
    return agent_routes.AgentRouteDependencies(
        auth=agent_routes.AgentAuthDependencies(
            require_agents=build_scope_dependency(
                runtime.authorized, "agents"
            ),
            schedule_run_gc=_app_attr("_schedule_run_gc"),
        ),
        catalog=agent_routes.AgentCatalogDependencies(
            model_to_tool=MODEL_TO_TOOL,
            model_to_agent_slug=_app_attr("_MODEL_TO_AGENT_SLUG"),
            agent_slug_to_tool=_app_attr("_AGENT_SLUG_TO_TOOL"),
            remote_agent_slugs=_app_attr("_REMOTE_AGENT_SLUGS"),
            legacy_aliases=_app_attr("_LEGACY_ALIASES"),
            serialize_capability=_app_attr("serialize_agent_capability"),
            conversation_context_enabled=getattr(
                adapters, "_conversation_context_enabled"
            ),
        ),
        chat=agent_routes.AgentChatDependencies(
            input=agent_routes.AgentChatInputDependencies(
                tool_for_model=tool_for_model,
                tool_accepts_obs=tool_accepts_obs,
                split_chat_messages=split_chat_messages,
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
            enabled=getattr(adapters, "_conversation_context_enabled"),
            executor=context_executor,
        ),
        upload=agent_routes.AgentUploadDependencies(
            resumable_service=upload_runtime.get_upload_service,
            asset_resolver=upload_runtime.get_asset_resolver,
            require_upload_control=require_explicit_scope(
                runtime.authorized, "files:delegate"
            ),
            schedule_cleanup=upload_runtime.schedule_cleanup,
            serialize_file_upload_capability=(
                upload_runtime.serialize_file_upload_capability
            ),
        ),
        tasks_db_path=_tasks_db_path,
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
        memory_agents = build_scope_dependency(runtime.authorized, "agents")
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
                require_agents=build_scope_dependency(
                    runtime.authorized, "agents"
                )
            ),
            context=run_routes.RunContextDependencies(
                current_user=_app_attr("current_request_user"),
                service_token_valid=_app_attr("is_service_token_valid"),
            ),
            projection=run_routes.RunProjectionDependencies(
                reconcile_task_logs=adapters.reconcile_task_logs,
                fetch_owner_run=adapters.fetch_owner_run,
                retry_owner_delivery=getattr(
                    adapters, "_retry_owner_delivery"
                ),
                list_owner_runs=adapters.list_owner_runs,
                strip_run_result=adapters.strip_run_result,
            ),
            pause=run_routes.RunPauseDependencies(
                a2ui_enabled=getattr(adapters, "_a2ui_enabled"),
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
    if not getattr(adapters, "_conversation_context_enabled")():
        return
    conversation_context_routes.register_conversation_context_routes(
        app,
        conversation_context_routes.ContextRouteDependencies(
            enabled=getattr(adapters, "_conversation_context_enabled"),
            require_agents=build_scope_dependency(
                runtime.authorized, "agents"
            ),
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


def build_app(
    *,
    context_executor: ConversationContextExecutor | None = None,
    run_registry_factory: Callable[[str], Any] | None = None,
    research_input_root_request_factory: (
        Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest] | None
    ) = None,
    research_input_runtime_required: bool = False,
) -> FastAPI:
    """Build the complete FastAPI application from typed route seams."""
    app = _build_base_app()
    runtime = _RuntimeState(rate_limit=_app_attr("make_rate_limiter")())
    app_support.bind_research_input_state(
        app, research_input_root_request_factory, runtime
    )
    scope = partial(build_scope_dependency, runtime.authorized)
    adapters = _RouteAdapters(
        runtime,
        run_registry_factory=(
            RunRegistry
            if run_registry_factory is None
            else run_registry_factory
        ),
        research_input_runtime_required=research_input_runtime_required,
    )
    context_executor = context_executor or build_context_executor(
        runtime,
        app_attr=_app_attr,
        api_config_factory=_api_config,
    )
    agent_dependencies = _build_agent_dependencies(
        runtime, adapters, context_executor
    )
    _register_interop_route(app, runtime, scope)
    _register_health_routes(app)
    agent_routes.register_model_route(app, agent_dependencies)
    _register_memory_and_admin_routes(app, runtime, adapters)
    agent_routes.register_agent_routes(app, agent_dependencies)
    _register_conversation_context_routes(
        app, runtime, adapters, context_executor
    )
    _register_run_routes(app, runtime, adapters)
    _register_a2a_routes(app, scope)
    app.include_router(_app_attr("create_relay_router")())
    register_error_handlers(app, _app_attr)
    return app
