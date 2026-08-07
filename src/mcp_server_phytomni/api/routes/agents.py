# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat, native-agent, expert-routing, and upload route registration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse, Response

from ...runtime.conversation_context.adapters import ContextAgentInvocation
from ...runtime.conversation_context.models import (
    ContextDelta,
    ConversationEnvelopeV1,
)
from ...runtime.conversation_context.service import (
    AgentOutcome,
    AsyncAgentAcceptance,
)
from ...runtime.locale import SupportedLocale, current_effective_locale
from ...runtime.stage_trace import DataStage, trace_data_stage
from ..advertised_protocols import serialize_protocols
from ..app_support import (
    build_safe_chat_request_info,
    resolve_http_locale,
)
from ..attachments import (
    redact_managed_attachment_values,
    redact_streaming_attachment_response,
)
from ..auth import ApiPrincipal
from ..schemas import (
    AgentRunRequest,
    ChatCompletionRequest,
    ExpertQueryRequest,
)
from . import agent_dependencies as _agent_dependencies
from .agent_dependencies import (
    AgentRouteDependencies,
    ContextNativeExecutionRequest,
)
from .attachment_inputs import (
    expert_attachment_requirement,
    filter_expert_attachment_candidates,
    prepare_chat_attachments,
    prepare_native_attachment_arguments,
    resolve_attachment_input,
    resolve_attachment_owner,
)
from .context_helpers import (
    context_response as _context_response,
)
from .context_helpers import (
    safe_native_request_json as _safe_native_request_json,
)
from .context_helpers import (
    slug_for_tool as _slug_for_tool,
)
from .context_types import (
    ContextAgentRequest,
    ContextLifecycleHttpRequest,
    execute_context_lifecycle,
    execute_context_lifecycle_http,
    inspect_context_replay,
)
from .expert_context import ExpertContextHelpers, execute_context_expert
from .uploads import AgentUploadDependencies, register_upload_routes

AgentAuthDependencies = _agent_dependencies.AgentAuthDependencies
AgentCatalogDependencies = _agent_dependencies.AgentCatalogDependencies
AgentChatDependencies = _agent_dependencies.AgentChatDependencies
AgentChatExecutionDependencies = (
    _agent_dependencies.AgentChatExecutionDependencies
)
AgentChatInputDependencies = _agent_dependencies.AgentChatInputDependencies
AgentChatProjectionDependencies = (
    _agent_dependencies.AgentChatProjectionDependencies
)
AgentContextDependencies = _agent_dependencies.AgentContextDependencies
AgentNativeDependencies = _agent_dependencies.AgentNativeDependencies


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
        tool_name = dependencies.chat.input.tool_for_model(payload.model)
        if tool_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"model not found: {payload.model}",
            )
        attachment_owner = resolve_attachment_owner(
            principal, payload.owner_subject
        )
        if payload.conversation is not None:
            if not dependencies.context.enabled():
                raise HTTPException(
                    status_code=404, detail="conversation context disabled"
                )
            return await _execute_context_chat(
                payload,
                dependencies,
                attachment_owner=attachment_owner,
            )
        resolved_input = resolve_attachment_input(
            payload.attachments,
            attachment_owner=attachment_owner,
            resolver=dependencies.upload.asset_resolver,
        )
        prepared = await _prepare_ordinary_chat_request(
            payload,
            request,
            dependencies,
            tool_name=tool_name,
            resolved_input=resolved_input,
        )
        if payload.stream:
            response = (
                await dependencies.chat.execution.stream_chat_completion(
                    tool_name=tool_name,
                    arguments=prepared["arguments"],
                    payload=payload,
                    user_query=prepared["user_query"],
                )
            )
            evidence = prepared["evidence"]
            if evidence is not None:
                return redact_streaming_attachment_response(response, evidence)
            return response
        if tool_name == "ReviewAgent":
            return await dependencies.chat.execution.review_chat_completion(
                payload=payload,
                arguments=prepared["arguments"],
                user_query=prepared["user_query"],
                attachment_evidence=prepared["evidence"],
            )
        return await _finalize_ordinary_chat_response(
            payload,
            dependencies,
            tool_name=tool_name,
            prepared=prepared,
        )


async def _prepare_ordinary_chat_request(
    payload: ChatCompletionRequest,
    request: Request,
    dependencies: AgentRouteDependencies,
    *,
    tool_name: str,
    resolved_input: Any,
) -> dict[str, Any]:
    """Flatten, resolve, and attach documents for one ordinary chat call."""
    accepts_obs = dependencies.chat.input.tool_accepts_obs(tool_name)
    if payload.obs_file_list and not accepts_obs:
        raise HTTPException(
            status_code=400,
            detail=f"model {payload.model} does not accept obs_file_list",
        )
    try:
        user_query = dependencies.chat.input.flatten_messages(payload.messages)
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
            brief_gene_resolver=dependencies.chat.input.brief_gene_resolver,
        )
    )
    arguments: dict[str, Any] = {
        "user_query": user_query,
        "locale": current_effective_locale(),
    }
    if accepts_obs:
        arguments["obs_file_list"] = payload.obs_file_list or []
    arguments, attachment_context = prepare_chat_attachments(
        tool_name=tool_name,
        arguments=arguments,
        resolved_input=resolved_input,
        db_path=dependencies.tasks_db_path(),
    )
    return {
        "user_query": user_query,
        "resolve_meta": resolve_meta,
        "arguments": arguments,
        "evidence": attachment_context.evidence,
    }


async def _finalize_ordinary_chat_response(
    payload: ChatCompletionRequest,
    dependencies: AgentRouteDependencies,
    *,
    tool_name: str,
    prepared: Mapping[str, Any],
) -> JSONResponse:
    """Invoke one ordinary chat agent and project its redacted completion."""
    envelope = await dependencies.chat.execution.invoke_tool_enveloped(
        tool_name, prepared["arguments"]
    )
    formatted_dict = _formatted_with_metadata(
        envelope, prepared["resolve_meta"]
    )
    envelope_dict = {
        "formatted": formatted_dict,
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
    }
    evidence = prepared["evidence"]
    if evidence is not None:
        envelope_dict = redact_managed_attachment_values(
            envelope_dict, evidence
        )
        formatted_dict = envelope_dict["formatted"]
    agent_slug = dependencies.catalog.model_to_agent_slug.get(payload.model)
    chat_run_id: str | None = None
    if agent_slug is not None:
        chat_run_id = dependencies.chat.projection.record_sync_run(
            agent=agent_slug,
            owner=dependencies.chat.projection.current_user() or "anonymous",
            result=envelope_dict,
            request_info=_chat_run_request_info(
                payload,
                prepared["user_query"],
                tool_name,
                current_effective_locale(),
            ),
        )
    if chat_run_id is None and agent_slug is not None:
        envelope_dict["execution"]["tracking"] = {"degraded": True}
    completion = dependencies.chat.projection.to_chat_completion(
        formatted_dict,
        envelope_dict.get("raw"),
        payload.model,
        envelope_dict["execution"],
    )
    if evidence is not None:
        completion = redact_managed_attachment_values(completion, evidence)
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
    *,
    attachment_owner: str,
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
    replay = await inspect_context_replay(
        executor=dependencies.context.executor, envelope=envelope
    )
    if replay is not None:
        return _context_response(replay, envelope)
    resolved_input = resolve_attachment_input(
        payload.attachments,
        attachment_owner=attachment_owner,
        resolver=dependencies.upload.asset_resolver,
    )
    prepared_attachments, attachment_context = prepare_chat_attachments(
        tool_name="ChatAgent",
        arguments={
            "user_query": envelope.current_message.content,
            "obs_file_list": list(payload.obs_file_list or []),
        },
        resolved_input=resolved_input,
        db_path=dependencies.tasks_db_path(),
    )
    attachment_arguments = {
        key: prepared_attachments[key]
        for key in ("obs_file_list", "data_list")
        if key in prepared_attachments
    }
    context_request = ContextAgentRequest(
        dialogue_id=None,
        request_json="{}",
        debug=dependencies.chat.projection.resolve_debug(payload.debug),
        obs_file_list=None,
        attachment_arguments=attachment_arguments,
        attachment_evidence=attachment_context.evidence,
    )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        if selected_agent_id != "ChatAgent":
            raise ValueError("instant context selected a non-chat agent")
        arguments = {
            **dispatch.arguments,
            **(context_request.attachment_arguments or {}),
        }
        evidence = context_request.attachment_evidence
        user_query, resolve_meta = (
            await dependencies.chat.input.resolve_chat_query(
                raw_query=arguments["user_query"],
                resolve_flag=bool(payload.resolve_gene_id),
                tool_name="ChatAgent",
                brief_gene_resolver=(
                    dependencies.chat.input.brief_gene_resolver
                ),
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
        if evidence is not None:
            envelope_dict = redact_managed_attachment_values(
                envelope_dict, evidence
            )
            formatted_dict = envelope_dict["formatted"]
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
            envelope_dict.get("raw"),
            payload.model,
            envelope_dict["execution"],
        )
        if evidence is not None:
            completion = redact_managed_attachment_values(completion, evidence)
        completion["run_id"] = chat_run_id
        if envelope_dict["execution"]["tracking"].get("degraded") is True:
            completion["degraded_tracking"] = True
        if not dependencies.chat.projection.resolve_debug(payload.debug):
            completion = dependencies.chat.projection.strip_chat_completion(
                completion
            )
        return AgentOutcome(
            result=completion,
        )

    async def delegate_async(
        _selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        _arguments: dict[str, Any],
    ) -> AsyncAgentAcceptance:
        raise AssertionError(
            "Instant context must not delegate asynchronously"
        )

    prepared = await execute_context_lifecycle(
        executor=dependencies.context.executor,
        envelope=envelope,
        invoke=invoke,
        delegate_async=delegate_async,
    )
    return _context_response(prepared, envelope)


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
    return build_safe_chat_request_info(
        payload,
        user_query,
        tool_name=tool_name,
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
    """Register native agent catalog, runs, and Expert routing."""

    @app.get("/v1/agents")
    async def list_agents(
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """List the agents reachable via ``/v1/agents/{slug}/runs``."""
        del principal
        payload: dict[str, Any] = {
            "object": "list",
            "file_upload": (
                dependencies.upload.serialize_file_upload_capability()
            ),
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
            "protocols": serialize_protocols(
                dependencies.catalog.conversation_context_enabled
            ),
        }
        return JSONResponse(payload)

    @app.post(
        "/v1/agents/{agent}/runs",
        dependencies=[
            Depends(dependencies.auth.schedule_run_gc),
            Depends(dependencies.upload.schedule_cleanup),
        ],
    )
    async def create_agent_run(
        agent: str,
        payload: AgentRunRequest,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Invoke one agent by slug and return its agent.run envelope."""
        if agent not in dependencies.catalog.agent_slug_to_tool:
            raise HTTPException(
                status_code=404, detail=f"agent not found: {agent}"
            )
        if agent == "data":
            async with trace_data_stage(
                DataStage.NATIVE_REQUEST,
                dependency="http",
            ):
                locale = resolve_http_locale(
                    explicit=payload.locale,
                    accept_language=request.headers.get("accept-language"),
                    latest_user_query=_latest_argument_query(
                        payload.arguments
                    ),
                )
        else:
            locale = resolve_http_locale(
                explicit=payload.locale,
                accept_language=request.headers.get("accept-language"),
                latest_user_query=_latest_argument_query(payload.arguments),
            )
        arguments = dict(payload.arguments)
        arguments["locale"] = locale
        attachment_owner = resolve_attachment_owner(
            principal, payload.owner_subject
        )
        request_json = _safe_native_request_json(
            dialogue_id=payload.dialogue_id,
            locale=locale,
            route=agent,
        )
        if payload.conversation is not None:
            if not dependencies.context.enabled():
                raise HTTPException(
                    status_code=404, detail="conversation context disabled"
                )
            return await _execute_context_native(
                ContextNativeExecutionRequest(
                    agent=agent,
                    payload=payload,
                    arguments=arguments,
                    attachment_owner=attachment_owner,
                    request_json=request_json,
                    dependencies=dependencies,
                )
            )
        resolved_input = resolve_attachment_input(
            payload.attachments,
            attachment_owner=attachment_owner,
            resolver=dependencies.upload.asset_resolver,
        )
        arguments, attachment_context = prepare_native_attachment_arguments(
            agent=agent,
            arguments=arguments,
            resolved_input=resolved_input,
            db_path=dependencies.tasks_db_path(),
        )
        body, status_code = await dependencies.native.invoke_agent_run(
            agent=agent,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            debug=dependencies.chat.projection.resolve_debug(payload.debug),
            request_json=request_json,
            attachment_evidence=attachment_context.evidence,
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
        attachment_owner = resolve_attachment_owner(
            principal, payload.owner_subject
        )
        if payload.conversation is not None:
            if not dependencies.context.enabled():
                raise HTTPException(
                    status_code=404, detail="conversation context disabled"
                )
            return await _execute_context_expert(
                payload,
                dependencies,
                attachment_owner=attachment_owner,
            )
        resolved_input = resolve_attachment_input(
            payload.attachments,
            attachment_owner=attachment_owner,
            resolver=dependencies.upload.asset_resolver,
        )
        requirement = expert_attachment_requirement(
            resolved_input,
            payload.obs_file_list,
        )
        payload = filter_expert_attachment_candidates(payload, requirement)
        resolve_http_locale(
            explicit=payload.locale,
            accept_language=request.headers.get("accept-language"),
            latest_user_query=payload.user_query,
        )
        body, status_code = await dependencies.native.route_expert_query(
            payload,
            debug=dependencies.chat.projection.resolve_debug(None),
            attachment_input=resolved_input,
        )
        return JSONResponse(body, status_code=status_code)


def _native_context_tool(
    agent: str,
    envelope: ConversationEnvelopeV1,
    dependencies: AgentRouteDependencies,
) -> str:
    """Validate and return the canonical tool selected by a native URL."""
    tool_name = dependencies.catalog.agent_slug_to_tool.get(agent)
    if tool_name is None:
        raise HTTPException(404, f"agent not found: {agent}")
    if envelope.mode != "expert":
        raise HTTPException(422, "native context requires expert mode")
    requested = envelope.requested_agent_id
    if requested is None:
        detail = "native context requires an explicit agent"
    elif requested != tool_name:
        detail = "native context agent does not match URL slug"
    elif tool_name not in envelope.allowed_agent_ids:
        detail = "native context agent is not allowed"
    else:
        return tool_name
    raise HTTPException(422, detail)


async def _execute_context_native(
    request: ContextNativeExecutionRequest,
) -> JSONResponse:
    """Execute one URL-pinned native agent through the V1 lifecycle."""
    agent = request.agent
    payload = request.payload
    dependencies = request.dependencies
    envelope = payload.conversation
    assert envelope is not None
    tool_name = _native_context_tool(agent, envelope, dependencies)
    replay = await inspect_context_replay(
        executor=dependencies.context.executor, envelope=envelope
    )
    if replay is not None:
        return _context_response(replay, envelope)
    resolved_input = resolve_attachment_input(
        payload.attachments,
        attachment_owner=request.attachment_owner,
        resolver=dependencies.upload.asset_resolver,
    )
    prepared_arguments, attachment_context = (
        prepare_native_attachment_arguments(
            agent=agent,
            arguments=request.arguments,
            resolved_input=resolved_input,
            db_path=dependencies.tasks_db_path(),
        )
    )
    attachment_arguments = {
        key: prepared_arguments[key]
        for key in ("obs_file_list", "data_list")
        if key in prepared_arguments
    }
    context_request = ContextAgentRequest(
        dialogue_id=payload.dialogue_id,
        request_json=request.request_json,
        debug=dependencies.chat.projection.resolve_debug(payload.debug),
        obs_file_list=None,
        attachment_arguments=attachment_arguments,
        attachment_evidence=attachment_context.evidence,
    )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        if selected_agent_id != tool_name:
            raise ValueError("native context selected a non-URL agent")
        return await _invoke_context_agent(
            selected_agent_id=selected_agent_id,
            dispatch=dispatch,
            request=context_request,
            dependencies=dependencies,
        )

    async def delegate_async(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        selected_arguments: dict[str, Any],
    ) -> AsyncAgentAcceptance:
        if selected_agent_id != tool_name:
            raise ValueError("native context selected a non-URL agent")
        arguments = {
            **selected_arguments,
            **(context_request.attachment_arguments or {}),
        }
        body, status_code = await dependencies.native.invoke_agent_run(
            agent=agent,
            arguments=arguments,
            attachment_evidence=context_request.attachment_evidence,
            dialogue_id=payload.dialogue_id,
            request_json=context_request.request_json,
            debug=context_request.debug,
        )
        return AsyncAgentAcceptance(body, status_code)

    prepared = await execute_context_lifecycle_http(
        ContextLifecycleHttpRequest(
            executor=dependencies.context.executor,
            envelope=envelope,
            invoke=invoke,
            delegate_async=delegate_async,
            selected_arguments=request.arguments,
            selection_failure_detail="invalid native context agent",
        )
    )
    return _context_response(prepared, envelope)


async def _execute_context_expert(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    *,
    attachment_owner: str,
) -> JSONResponse:
    """Delegate Expert context execution to the extracted helper module."""
    return await execute_context_expert(
        payload,
        dependencies,
        attachment_owner=attachment_owner,
        helpers=ExpertContextHelpers(
            invoke_context_agent=_invoke_context_agent,
        ),
    )


def _context_clarification_outcome(
    selected_agent_id: str,
    slug: str,
    dispatch: ContextAgentInvocation,
) -> AgentOutcome | None:
    """Return a terminal clarification outcome when one was requested."""
    if selected_agent_id not in {"KnowledgeAgent", "ReviewAgent"}:
        return None
    clarification = dispatch.private_agent_state.get("clarification_message")
    if not isinstance(clarification, str) or not clarification.strip():
        return None
    status = "failed" if selected_agent_id == "ReviewAgent" else "succeeded"
    delta = ContextDelta() if status == "succeeded" else None
    return AgentOutcome(
        result=_clarification_agent_run(slug, clarification),
        status=status,
        context_delta=delta,
    )


def _context_adapter(
    selected_agent_id: str,
    private_agent_state: dict[str, Any],
) -> Any:
    """Extract the adapter belonging to one selected context agent."""
    if selected_agent_id == "KnowledgeAgent":
        return private_agent_state.pop("knowledge_adapter", None)
    adapter_keys = {
        "DataAgent": "data_adapter",
        "BriefGeneAgent": "brief_gene_adapter",
        "ReviewAgent": "review_adapter",
    }
    key = adapter_keys.get(selected_agent_id)
    return None if key is None else private_agent_state.get(key)


def _context_execution_thread_id(
    selected_agent_id: str,
    dispatch: ContextAgentInvocation,
    adapter: Any,
) -> str | None:
    """Select the durable execution thread for a context invocation."""
    thread_id = dispatch.agent_thread_id
    if selected_agent_id == "ReviewAgent" and adapter is not None:
        thread_id = getattr(adapter, "execution_thread_id", thread_id)
    if selected_agent_id in {
        "ChatAgent",
        "KnowledgeAgent",
        "DataAgent",
        "ReviewAgent",
    }:
        return thread_id
    return None


async def _context_success_outcome(
    selected_agent_id: str,
    body: dict[str, Any],
    adapter: Any,
) -> AgentOutcome:
    """Shape a successful native response and any context delta."""
    if adapter is None:
        outcome = AgentOutcome(result=body)
    elif selected_agent_id != "ReviewAgent":
        outcome = AgentOutcome(
            result=body,
            context_delta=(
                adapter.delta(body)
                if selected_agent_id
                in {"KnowledgeAgent", "DataAgent", "BriefGeneAgent"}
                else None
            ),
        )
    elif (
        not getattr(adapter, "settlement_ready", False)
        or not await adapter.validate_settlement_candidate()
    ):
        outcome = AgentOutcome(result=body, status="failed")
    else:
        settlement_metadata = adapter.settlement_metadata()
        if settlement_metadata is None:
            adapter.mark_failed()
            outcome = AgentOutcome(result=body, status="failed")
        else:
            outcome = AgentOutcome(
                result=body,
                context_delta=adapter.delta(body),
                private_stage_metadata=settlement_metadata,
            )
    return outcome


async def _invoke_context_agent(
    *,
    selected_agent_id: str,
    dispatch: ContextAgentInvocation,
    request: ContextAgentRequest,
    dependencies: AgentRouteDependencies,
) -> AgentOutcome:
    """Invoke one selected agent and shape its context outcome."""
    slug = _slug_for_tool(selected_agent_id, dependencies)
    clarification = _context_clarification_outcome(
        selected_agent_id, slug, dispatch
    )
    if clarification is not None:
        return clarification
    arguments = dict(dispatch.arguments)
    if request.obs_file_list is not None and (
        dependencies.chat.input.tool_accepts_obs(selected_agent_id)
    ):
        arguments["obs_file_list"] = list(request.obs_file_list)
    arguments.update(request.attachment_arguments or {})
    private_agent_state = dict(dispatch.private_agent_state)
    adapter = _context_adapter(selected_agent_id, private_agent_state)
    body, status_code = await dependencies.native.invoke_agent_run(
        agent=slug,
        arguments=arguments,
        conversation_messages=dispatch.conversation_messages,
        agent_thread_id=_context_execution_thread_id(
            selected_agent_id, dispatch, adapter
        ),
        private_agent_state=private_agent_state or None,
        dialogue_id=request.dialogue_id,
        request_json=request.request_json,
        debug=request.debug,
        attachment_evidence=request.attachment_evidence,
    )
    if status_code != 200 or body.get("status") != "succeeded":
        return AgentOutcome(result=body, status="running")
    return await _context_success_outcome(selected_agent_id, body, adapter)


def _clarification_agent_run(agent: str, message: str) -> dict[str, Any]:
    """Return a sync agent.run envelope for clarification-only turns."""
    return {
        "id": None,
        "object": "agent.run",
        "agent": agent,
        "status": "succeeded",
        "task_ids": [],
        "result": {
            "formatted": {
                "answer": message,
                "follow_up_questions": [],
                "references": [],
            }
        },
    }


def register_agent_routes(
    app: FastAPI,
    dependencies: AgentRouteDependencies,
) -> None:
    """Register primary agent routes in their legacy order."""
    _register_chat_route(app, dependencies)
    _register_native_routes(app, dependencies)
    register_upload_routes(app, dependencies.upload)


__all__ = [
    *_agent_dependencies.__all__,
    "AgentUploadDependencies",
    "register_agent_routes",
    "register_model_route",
]
