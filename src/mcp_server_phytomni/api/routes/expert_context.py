# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert conversation-context attachment filtering and invocation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ...runtime.conversation_context.adapters import ContextAgentInvocation
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    AgentOutcome,
    AsyncAgentAcceptance,
)
from ...runtime.locale import current_effective_locale
from ..agent_capabilities import filter_tools_for_attachment_channels
from ..attachments import redact_managed_attachment_values
from ..schemas import ExpertQueryRequest
from .agent_dependencies import AgentRouteDependencies
from .attachment_inputs import (
    attachment_not_supported_error,
    expert_attachment_channels,
    expert_attachment_requirement,
    filter_expert_attachment_candidates,
    prepare_selected_expert_arguments,
    resolve_attachment_input,
)
from .context_helpers import (
    context_response,
    safe_native_request_json,
    slug_for_tool,
)
from .context_types import (
    ContextAgentRequest,
    ContextLifecycleHttpRequest,
    execute_context_lifecycle_http,
    inspect_context_replay,
)

type InvokeContextAgent = Callable[..., Awaitable[AgentOutcome]]


@dataclass(frozen=True, slots=True)
class ExpertContextHelpers:
    """Callables injected from ``agents`` without creating an import cycle."""

    invoke_context_agent: InvokeContextAgent


@dataclass(frozen=True, slots=True)
class PreparedExpertAttachments:
    """Prevalidated attachment-only state for one eligible Expert tool."""

    arguments: Mapping[str, Any]
    evidence: Any


@dataclass(frozen=True, slots=True)
class _ExpertContextInvokeRequest:
    """Inputs for one sync Expert context agent invocation."""

    selected_agent_id: str
    dispatch: ContextAgentInvocation
    payload: ExpertQueryRequest
    dependencies: AgentRouteDependencies
    attachments: PreparedExpertAttachments
    context_request: ContextAgentRequest
    helpers: ExpertContextHelpers


@dataclass(frozen=True, slots=True)
class _ExpertContextDelegateRequest:
    """Inputs for one remote Expert context acceptance."""

    selected_agent_id: str
    arguments: dict[str, Any]
    payload: ExpertQueryRequest
    dependencies: AgentRouteDependencies
    attachments: PreparedExpertAttachments
    context_request: ContextAgentRequest


__all__ = ["ExpertContextHelpers", "execute_context_expert"]


async def execute_context_expert(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    *,
    attachment_owner: str,
    helpers: ExpertContextHelpers,
) -> JSONResponse:
    """Run constrained Expert V1 selection through the shared lifecycle."""
    envelope = payload.conversation
    assert envelope is not None
    if envelope.mode != "expert":
        raise HTTPException(
            status_code=422, detail="expert context requires expert mode"
        )
    replay = await inspect_context_replay(
        executor=dependencies.context.executor, envelope=envelope
    )
    if replay is not None:
        return context_response(replay, envelope)
    payload, envelope, prepared_by_tool = _prepare_expert_context_inputs(
        payload, dependencies, attachment_owner, envelope
    )
    request_json = safe_native_request_json(
        dialogue_id=payload.dialogue_id,
        locale=current_effective_locale(),
        route="expert",
    )
    context_request = ContextAgentRequest(
        dialogue_id=payload.dialogue_id,
        request_json=request_json,
        debug=dependencies.chat.projection.resolve_debug(None),
        obs_file_list=None,
    )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        attachments = prepared_by_tool.get(selected_agent_id)
        if attachments is None:
            raise ValueError("router selected an unprepared Expert tool")
        return await _invoke_context_expert_agent(
            _ExpertContextInvokeRequest(
                selected_agent_id=selected_agent_id,
                dispatch=dispatch,
                payload=payload,
                dependencies=dependencies,
                attachments=attachments,
                context_request=context_request,
                helpers=helpers,
            )
        )

    async def delegate_async(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        arguments: dict[str, Any],
    ) -> AsyncAgentAcceptance:
        attachments = prepared_by_tool.get(selected_agent_id)
        if attachments is None:
            raise ValueError("router selected an unprepared Expert tool")
        return await _delegate_context_expert_async(
            _ExpertContextDelegateRequest(
                selected_agent_id=selected_agent_id,
                arguments=arguments,
                payload=payload,
                dependencies=dependencies,
                attachments=attachments,
                context_request=context_request,
            )
        )

    prepared = await execute_context_lifecycle_http(
        ContextLifecycleHttpRequest(
            envelope=envelope,
            invoke=invoke,
            executor=dependencies.context.executor,
            delegate_async=delegate_async,
            selection_failure_detail=(
                "router did not resolve one permitted agent"
            ),
        )
    )
    return context_response(prepared, envelope)


def _prepare_expert_context_inputs(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    attachment_owner: str,
    envelope: ConversationEnvelopeV1,
) -> tuple[
    ExpertQueryRequest,
    ConversationEnvelopeV1,
    dict[str, PreparedExpertAttachments],
]:
    """Resolve and validate every eligible Expert attachment projection."""
    resolved_input = resolve_attachment_input(
        payload.attachments,
        attachment_owner=attachment_owner,
        resolver=dependencies.upload.asset_resolver,
    )
    payload = filter_expert_attachment_candidates(
        payload,
        expert_attachment_requirement(resolved_input, payload.obs_file_list),
    )
    ordered_tools = _ordered_expert_tools(
        payload,
        envelope,
        expert_attachment_channels(resolved_input, payload.obs_file_list),
    )
    return (
        payload,
        envelope.model_copy(update={"allowed_agent_ids": ordered_tools}),
        _prepare_expert_attachments(
            payload, dependencies, ordered_tools, resolved_input
        ),
    )


def _ordered_expert_tools(
    payload: ExpertQueryRequest,
    envelope: ConversationEnvelopeV1,
    channels: frozenset[str],
) -> list[str]:
    """Intersect payload order with context authorization and channels."""
    conversation_allowed = set(envelope.allowed_agent_ids)
    ordered = [
        tool for tool in payload.allowed_tools if tool in conversation_allowed
    ]
    if channels:
        ordered_tools = list(
            filter_tools_for_attachment_channels(
                allowed_tools=ordered,
                channels=channels,
            )
        )
    else:
        ordered_tools = ordered
    if not ordered_tools or (
        envelope.requested_agent_id is not None
        and envelope.requested_agent_id not in ordered_tools
    ):
        raise attachment_not_supported_error()
    return ordered_tools


def _prepare_expert_attachments(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    ordered_tools: list[str],
    resolved_input: Any,
) -> dict[str, PreparedExpertAttachments]:
    """Prepare trusted attachment maps in the eligible-tool order."""
    prepared_by_tool: dict[str, PreparedExpertAttachments] = {}
    for tool_name in ordered_tools:
        slug = slug_for_tool(tool_name, dependencies)
        arguments, context = prepare_selected_expert_arguments(
            agent=slug,
            selected_arguments={},
            payload=payload,
            resolved_input=resolved_input,
            db_path=dependencies.tasks_db_path(),
        )
        prepared_by_tool[tool_name] = PreparedExpertAttachments(
            arguments={
                key: arguments[key]
                for key in ("obs_file_list", "data_list")
                if key in arguments
            },
            evidence=context.evidence,
        )
    return prepared_by_tool


def _expert_context_arguments(
    agent: str,
    arguments: Mapping[str, Any],
    payload: ExpertQueryRequest,
) -> dict[str, Any]:
    """Merge canonical selector fields without touching attachment state."""
    prepared = dict(arguments)
    if agent == "analyst":
        prepared["goal_description"] = payload.user_query
        prepared.pop("user_query", None)
    elif agent in {
        "chat",
        "knowledge",
        "data",
        "review",
        "brief_gene",
        "research",
    }:
        prepared["user_query"] = payload.user_query
        prepared.pop("goal_description", None)
    prepared["locale"] = current_effective_locale()
    return prepared


async def _delegate_context_expert_async(
    request: _ExpertContextDelegateRequest,
) -> AsyncAgentAcceptance:
    """Prepare and accept one remote Expert agent through context V1."""
    slug = slug_for_tool(request.selected_agent_id, request.dependencies)
    prepared_arguments = {
        **_expert_context_arguments(slug, request.arguments, request.payload),
        **request.attachments.arguments,
    }
    body, status_code = await request.dependencies.native.invoke_agent_run(
        agent=slug,
        arguments=prepared_arguments,
        dialogue_id=request.context_request.dialogue_id,
        request_json=request.context_request.request_json,
        debug=request.context_request.debug,
        attachment_evidence=request.attachments.evidence,
    )
    if request.attachments.evidence is not None:
        body = redact_managed_attachment_values(
            body, request.attachments.evidence
        )
    return AsyncAgentAcceptance(body, status_code)


async def _invoke_context_expert_agent(
    request: _ExpertContextInvokeRequest,
) -> AgentOutcome:
    """Invoke one selected Expert agent through the shared context seam."""
    slug = slug_for_tool(request.selected_agent_id, request.dependencies)
    outcome = await request.helpers.invoke_context_agent(
        selected_agent_id=request.selected_agent_id,
        dispatch=replace(
            request.dispatch,
            arguments={
                **_expert_context_arguments(
                    slug, request.dispatch.arguments, request.payload
                ),
                **request.attachments.arguments,
            },
        ),
        request=ContextAgentRequest(
            dialogue_id=request.context_request.dialogue_id,
            request_json=request.context_request.request_json,
            debug=request.context_request.debug,
            obs_file_list=None,
            attachment_arguments=request.attachments.arguments,
            attachment_evidence=request.attachments.evidence,
        ),
        dependencies=request.dependencies,
    )
    if request.attachments.evidence is not None:
        return AgentOutcome(
            result=redact_managed_attachment_values(
                outcome.result, request.attachments.evidence
            ),
            assistant_summary=outcome.assistant_summary,
            context_delta=outcome.context_delta,
            context_delta_error=outcome.context_delta_error,
            status=outcome.status,
            private_stage_metadata=outcome.private_stage_metadata,
        )
    return outcome
