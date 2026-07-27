# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat, native-agent, expert-routing, and upload route registration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response

from ...agents.expert import ToolSelectionError
from ...runtime.conversation_context.adapters import (
    ContextAgentInvocation,
    ConversationContextExecutor,
)
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    AgentOutcome,
    PreparedTurn,
    PrepareStatus,
)
from ...runtime.locale import SupportedLocale, current_effective_locale
from ...runtime.run_registry import RunRequestInfo
from ..app_support import resolve_http_locale
from ..auth import ApiPrincipal
from ..lifecycle_contract import SafeApiError
from ..schemas import (
    AgentRunRequest,
    ChatCompletionRequest,
    ExpertQueryRequest,
    FileUploadResponse,
    UploadPurpose,
)

type AgentRun = Callable[..., Awaitable[tuple[dict[str, Any], int]]]
type ChatResponse = Callable[..., Awaitable[Response]]
type QueryFlattener = Callable[[Any], str]
type ChatResolver = Callable[..., Awaitable[tuple[str, dict[str, Any]]]]
type ErrorResponse = Callable[..., JSONResponse]


@dataclass(frozen=True, slots=True)
class AgentAuthDependencies:
    """Authentication and request-lifecycle dependencies."""

    require_agents: Callable[..., Any]
    schedule_run_gc: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AgentCatalogDependencies:
    """Public model and agent catalog projections."""

    model_to_tool: Mapping[str, str]
    model_to_agent_slug: Mapping[str, str]
    agent_slug_to_tool: Mapping[str, str]
    remote_agent_slugs: frozenset[str]
    legacy_aliases: Mapping[str, list[str]]
    serialize_capability: Callable[[str], Any]
    conversation_context_enabled: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class AgentChatInputDependencies:
    """Chat request validation and HTTP-only pre-shaping seams."""

    tool_for_model: Callable[[str], str | None]
    tool_accepts_obs: Callable[[str], bool]
    flatten_messages: QueryFlattener
    resolve_chat_query: ChatResolver
    brief_gene_resolver: Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class AgentChatExecutionDependencies:
    """Chat execution seams shared by sync, stream, and Review paths."""

    invoke_tool_enveloped: Callable[..., Awaitable[Any]]
    stream_chat_completion: ChatResponse
    review_chat_completion: ChatResponse


@dataclass(frozen=True, slots=True)
class AgentChatProjectionDependencies:
    """Run-registry and OpenAI response projection seams."""

    record_sync_run: Callable[..., str | None]
    current_user: Callable[[], str | None]
    to_chat_completion: Callable[..., dict[str, Any]]
    strip_chat_completion: Callable[[dict[str, Any]], dict[str, Any]]
    resolve_debug: Callable[[bool | None], bool]


@dataclass(frozen=True, slots=True)
class AgentChatDependencies:
    """Grouped dependencies for the OpenAI-compatible chat route."""

    input: AgentChatInputDependencies
    execution: AgentChatExecutionDependencies
    projection: AgentChatProjectionDependencies


@dataclass(frozen=True, slots=True)
class AgentNativeDependencies:
    """Native-agent and Expert routing call seams."""

    invoke_agent_run: AgentRun
    route_expert_query: AgentRun


@dataclass(frozen=True, slots=True)
class AgentContextDependencies:
    """Conversation-context protocol gate and shared executor."""

    enabled: Callable[[], bool]
    executor: ConversationContextExecutor


@dataclass(frozen=True, slots=True)
class AgentUploadDependencies:
    """Multipart upload and error projection seams."""

    handle_file_upload: Callable[
        ..., Awaitable[FileUploadResponse | JSONResponse]
    ]
    error_response: ErrorResponse


@dataclass(frozen=True, slots=True)
class AgentRouteDependencies:
    """Explicit dependencies required by the primary agent routes."""

    auth: AgentAuthDependencies
    catalog: AgentCatalogDependencies
    chat: AgentChatDependencies
    native: AgentNativeDependencies
    context: AgentContextDependencies
    upload: AgentUploadDependencies


def register_model_route(
    app: FastAPI,
    dependencies: AgentRouteDependencies,
) -> None:
    """Register the OpenAI-compatible model catalog endpoint."""

    @app.get("/v1/models")
    async def list_models(
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """List the chat-like model ids (OpenAI convention)."""
        del principal
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "object": "model",
                        "owned_by": "phytomni",
                    }
                    for model_id in dependencies.catalog.model_to_tool
                ],
            }
        )


def _register_chat_route(
    app: FastAPI,
    dependencies: AgentRouteDependencies,
) -> None:
    """Register the OpenAI-compatible chat completion endpoint."""

    @app.post(
        "/v1/chat/completions",
        dependencies=[Depends(dependencies.auth.schedule_run_gc)],
    )
    async def chat_completions(
        payload: ChatCompletionRequest,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> Response:
        """Run a chat-like agent in an OpenAI-compatible shape.

        With ``stream=true`` and a streaming-capable model, returns a
        ``text/event-stream`` carrying ``data: {...}\\n\\n`` chunks
        plus a terminating ``data: [DONE]\\n\\n``; the non-stream
        path returns a JSON ``chat.completion`` envelope unchanged.
        """
        del principal
        tool_name = dependencies.chat.input.tool_for_model(payload.model)
        if tool_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"model not found: {payload.model}",
            )
        if payload.conversation is not None:
            if not dependencies.context.enabled():
                raise HTTPException(
                    status_code=404, detail="conversation context disabled"
                )
            return await _execute_context_chat(payload, dependencies)
        accepts_obs = dependencies.chat.input.tool_accepts_obs(tool_name)
        if payload.obs_file_list and not accepts_obs:
            raise HTTPException(
                status_code=400,
                detail=f"model {payload.model} does not accept "
                "obs_file_list",
            )
        try:
            user_query = dependencies.chat.input.flatten_messages(
                payload.messages
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolve_http_locale(
            explicit=payload.locale,
            accept_language=request.headers.get("accept-language"),
            latest_user_query=user_query,
        )
        user_query, resolve_meta = (
            await dependencies.chat.input.resolve_chat_query(
                raw_query=user_query,
                resolve_flag=bool(payload.resolve_gene_id),
                tool_name=tool_name,
                brief_gene_resolver=(
                    dependencies.chat.input.brief_gene_resolver
                ),
            )
        )
        arguments: dict[str, Any] = {
            "user_query": user_query,
            "locale": current_effective_locale(),
        }
        if accepts_obs:
            arguments["obs_file_list"] = payload.obs_file_list or []
        if payload.stream:
            return await dependencies.chat.execution.stream_chat_completion(
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
                user_query=user_query,
            )
        if tool_name == "ReviewAgent":
            return await dependencies.chat.execution.review_chat_completion(
                payload=payload,
                arguments=arguments,
                user_query=user_query,
            )
        envelope = await dependencies.chat.execution.invoke_tool_enveloped(
            tool_name, arguments
        )
        formatted_dict = _formatted_with_metadata(envelope, resolve_meta)
        envelope_dict = {
            "formatted": formatted_dict,
            "execution": asdict(envelope.execution),
            "raw": envelope.raw,
        }
        agent_slug = dependencies.catalog.model_to_agent_slug.get(
            payload.model
        )
        chat_run_id: str | None = None
        if agent_slug is not None:
            chat_run_id = dependencies.chat.projection.record_sync_run(
                agent=agent_slug,
                owner=(
                    dependencies.chat.projection.current_user() or "anonymous"
                ),
                result=envelope_dict,
                request_info=_chat_run_request_info(
                    payload,
                    user_query,
                    tool_name,
                    current_effective_locale(),
                ),
            )
        if chat_run_id is None and agent_slug is not None:
            envelope_dict["execution"]["tracking"] = {"degraded": True}
        completion = dependencies.chat.projection.to_chat_completion(
            formatted_dict,
            envelope.raw,
            payload.model,
            envelope_dict["execution"],
        )
        completion["run_id"] = chat_run_id
        if envelope_dict["execution"]["tracking"].get("degraded") is True:
            completion["degraded_tracking"] = True
        if not dependencies.chat.projection.resolve_debug(payload.debug):
            completion = dependencies.chat.projection.strip_chat_completion(
                completion
            )
        return JSONResponse(completion)


async def _execute_context_chat(
    payload: ChatCompletionRequest,
    dependencies: AgentRouteDependencies,
) -> JSONResponse:
    """Execute an Instant V1 completion without flattening legacy messages."""
    envelope = payload.conversation
    assert envelope is not None
    if envelope.mode != "instant":
        raise HTTPException(
            status_code=422, detail="chat context requires instant mode"
        )
    if dependencies.catalog.model_to_tool[payload.model] != "ChatAgent":
        raise HTTPException(
            status_code=422,
            detail="instant context requires a ChatAgent model",
        )
    if payload.stream:
        raise HTTPException(
            status_code=400,
            detail="conversation context streaming is not available",
        )
    if payload.obs_file_list and not dependencies.chat.input.tool_accepts_obs(
        "ChatAgent"
    ):
        raise HTTPException(
            status_code=400,
            detail=f"model {payload.model} does not accept obs_file_list",
        )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        if selected_agent_id != "ChatAgent":
            raise ValueError("instant context selected a non-chat agent")
        arguments = dict(dispatch.arguments)
        arguments["obs_file_list"] = list(payload.obs_file_list or [])
        user_query, resolve_meta = (
            await dependencies.chat.input.resolve_chat_query(
                raw_query=arguments["user_query"],
                resolve_flag=bool(payload.resolve_gene_id),
                tool_name="ChatAgent",
                brief_gene_resolver=dependencies.chat.input.brief_gene_resolver,
            )
        )
        arguments["user_query"] = user_query
        tool_envelope = (
            await dependencies.chat.execution.invoke_tool_enveloped(
                "ChatAgent",
                arguments,
                conversation_messages=dispatch.conversation_messages,
                agent_thread_id=dispatch.agent_thread_id,
            )
        )
        formatted_dict = _formatted_with_metadata(tool_envelope, resolve_meta)
        envelope_dict = {
            "formatted": formatted_dict,
            "execution": asdict(tool_envelope.execution),
            "raw": tool_envelope.raw,
        }
        chat_run_id = dependencies.chat.projection.record_sync_run(
            agent="chat",
            owner=dependencies.chat.projection.current_user() or "anonymous",
            result=envelope_dict,
            request_info=_chat_run_request_info(
                payload,
                user_query,
                "ChatAgent",
                current_effective_locale(),
            ),
        )
        if chat_run_id is None:
            envelope_dict["execution"]["tracking"] = {"degraded": True}
        completion = dependencies.chat.projection.to_chat_completion(
            formatted_dict,
            tool_envelope.raw,
            payload.model,
            envelope_dict["execution"],
        )
        completion["run_id"] = chat_run_id
        if envelope_dict["execution"]["tracking"].get("degraded") is True:
            completion["degraded_tracking"] = True
        if not dependencies.chat.projection.resolve_debug(payload.debug):
            completion = dependencies.chat.projection.strip_chat_completion(
                completion
            )
        answer = formatted_dict.get("answer")
        return AgentOutcome(
            result=completion,
            assistant_summary=answer if isinstance(answer, str) else None,
        )

    async def delegate_async(
        _selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        _arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError(
            "Instant context must not delegate asynchronously"
        )

    prepared = await dependencies.context.executor.execute(
        envelope=envelope,
        invoke=invoke,
        delegate_async=delegate_async,
    )
    return _context_response(prepared, envelope)


def _context_response(
    prepared: PreparedTurn,
    envelope: ConversationEnvelopeV1,
) -> JSONResponse:
    """Return a staged terminal payload or a bounded context retry signal."""
    if prepared.status is PrepareStatus.REBUILD_REQUIRED:
        raise SafeApiError(
            status_code=409,
            code="conversation_context_rebuild_required",
            message="conversation context rebuild required",
            stage="context",
            retryable=True,
        )
    if prepared.status is PrepareStatus.IN_PROGRESS:
        raise SafeApiError(
            status_code=409,
            code="conversation_context_turn_in_progress",
            message="conversation context turn in progress",
            stage="context",
            retryable=True,
        )
    if prepared.result is None:
        raise HTTPException(
            status_code=500, detail="conversation context failed"
        )
    response = dict(prepared.result)
    if prepared.stage is not None:
        response["conversation_context"] = {
            "schema_version": 1,
            "turn_id": envelope.turn_id,
            **asdict(prepared.stage),
        }
    status_code = 202 if response.get("status") == "running" else 200
    return JSONResponse(response, status_code=status_code)


def _formatted_with_metadata(
    envelope: Any,
    resolve_meta: dict[str, Any],
) -> dict[str, Any]:
    """Project one tool envelope and merge resolver metadata."""
    formatted_dict = asdict(envelope.formatted)
    if not resolve_meta:
        return formatted_dict
    existing_meta = formatted_dict.get("metadata")
    if isinstance(existing_meta, dict):
        existing_meta.update(resolve_meta)
    else:
        formatted_dict["metadata"] = dict(resolve_meta)
    return formatted_dict


def _chat_run_request_info(
    payload: ChatCompletionRequest,
    user_query: str,
    tool_name: str,
    locale: SupportedLocale,
) -> Any:
    """Build the run-registry request record without app-layer imports."""
    return RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name=tool_name,
        model=payload.model,
        request_json=payload.model_dump_json(),
        locale=locale,
    )


def _latest_argument_query(arguments: Mapping[str, Any]) -> str:
    """Find the latest nonblank user-facing query in native arguments."""
    for key in ("user_query", "query", "research_topic"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _register_native_routes(
    app: FastAPI,
    dependencies: AgentRouteDependencies,
) -> None:
    """Register native agent catalog, runs, Expert routing, and files."""

    @app.get("/v1/agents")
    async def list_agents(
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """List the agents reachable via ``/v1/agents/{slug}/runs``."""
        del principal
        payload: dict[str, Any] = {
            "object": "list",
            "data": [
                {
                    "slug": slug,
                    "tool": tool,
                    "origin": (
                        "remote"
                        if slug in dependencies.catalog.remote_agent_slugs
                        else "local"
                    ),
                    "legacy_aliases": (
                        dependencies.catalog.legacy_aliases.get(tool, [])
                    ),
                    "capabilities": (
                        dependencies.catalog.serialize_capability(slug)
                    ),
                }
                for slug, tool in (
                    dependencies.catalog.agent_slug_to_tool.items()
                )
            ],
        }
        if dependencies.catalog.conversation_context_enabled():
            payload["protocols"] = {"conversation_context": [1]}
        return JSONResponse(payload)

    @app.post(
        "/v1/agents/{agent}/runs",
        dependencies=[Depends(dependencies.auth.schedule_run_gc)],
    )
    async def create_agent_run(
        agent: str,
        payload: AgentRunRequest,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Invoke one agent by slug and return its agent.run envelope."""
        del principal
        locale = resolve_http_locale(
            explicit=payload.locale,
            accept_language=request.headers.get("accept-language"),
            latest_user_query=_latest_argument_query(payload.arguments),
        )
        arguments = dict(payload.arguments)
        arguments["locale"] = locale
        body, status_code = await dependencies.native.invoke_agent_run(
            agent=agent,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            debug=dependencies.chat.projection.resolve_debug(payload.debug),
            request_json=payload.model_dump_json(),
        )
        return JSONResponse(body, status_code=status_code)

    @app.post(
        "/v1/query/route",
        dependencies=[Depends(dependencies.auth.schedule_run_gc)],
    )
    async def route_query(
        payload: ExpertQueryRequest,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Autonomously route a query to an agent and return its run."""
        del principal
        if payload.conversation is not None:
            if not dependencies.context.enabled():
                raise HTTPException(
                    status_code=404, detail="conversation context disabled"
                )
            return await _execute_context_expert(payload, dependencies)
        resolve_http_locale(
            explicit=payload.locale,
            accept_language=request.headers.get("accept-language"),
            latest_user_query=payload.user_query,
        )
        body, status_code = await dependencies.native.route_expert_query(
            payload, debug=dependencies.chat.projection.resolve_debug(None)
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
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
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
        return await dependencies.upload.handle_file_upload(
            request=request,
            file=file,
            purpose=purpose,
            user_id=principal.user_id,
            error_response=dependencies.upload.error_response,
        )


async def _execute_context_expert(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
) -> JSONResponse:
    """Run constrained Expert V1 selection through the shared lifecycle."""
    envelope = payload.conversation
    assert envelope is not None
    if envelope.mode != "expert":
        raise HTTPException(
            status_code=422, detail="expert context requires expert mode"
        )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        slug = _slug_for_tool(selected_agent_id, dependencies)
        body, status_code = await dependencies.native.invoke_agent_run(
            agent=slug,
            arguments=dispatch.arguments,
            conversation_messages=dispatch.conversation_messages,
            dialogue_id=payload.dialogue_id,
            request_json=payload.model_dump_json(),
            debug=dependencies.chat.projection.resolve_debug(None),
        )
        if status_code != 200 or body.get("status") != "succeeded":
            return AgentOutcome(result=body, status="running")
        return AgentOutcome(
            result=body,
            assistant_summary=_agent_response_summary(body),
        )

    async def delegate_async(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        slug = _slug_for_tool(selected_agent_id, dependencies)
        body, _status_code = await dependencies.native.invoke_agent_run(
            agent=slug,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            request_json=payload.model_dump_json(),
            debug=dependencies.chat.projection.resolve_debug(None),
        )
        return body

    try:
        prepared = await dependencies.context.executor.execute(
            envelope=envelope,
            invoke=invoke,
            delegate_async=delegate_async,
        )
    except (ToolSelectionError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="router did not resolve one permitted agent",
        ) from exc
    return _context_response(prepared, envelope)


def _slug_for_tool(
    tool_name: str,
    dependencies: AgentRouteDependencies,
) -> str:
    """Map a canonical selected tool back to the public native slug."""
    for slug, candidate in dependencies.catalog.agent_slug_to_tool.items():
        if candidate == tool_name:
            return slug
    raise ValueError("selected agent is unavailable")


def _agent_response_summary(body: Mapping[str, Any]) -> str | None:
    """Extract a bounded terminal answer summary without exposing raw state."""
    result = body.get("result")
    if not isinstance(result, Mapping):
        return None
    formatted = result.get("formatted")
    if not isinstance(formatted, Mapping):
        return None
    answer = formatted.get("answer")
    return answer if isinstance(answer, str) else None


def register_agent_routes(
    app: FastAPI,
    dependencies: AgentRouteDependencies,
) -> None:
    """Register primary agent routes in their legacy order."""
    _register_chat_route(app, dependencies)
    _register_native_routes(app, dependencies)


__all__ = [
    "AgentAuthDependencies",
    "AgentCatalogDependencies",
    "AgentContextDependencies",
    "AgentChatDependencies",
    "AgentChatExecutionDependencies",
    "AgentChatInputDependencies",
    "AgentChatProjectionDependencies",
    "AgentNativeDependencies",
    "AgentRouteDependencies",
    "AgentUploadDependencies",
    "register_agent_routes",
    "register_model_route",
]
