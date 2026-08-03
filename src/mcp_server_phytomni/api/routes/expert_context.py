# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert conversation-context attachment filtering and invocation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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
)

type InvokeContextAgent = Callable[..., Awaitable[AgentOutcome]]


@dataclass(frozen=True, slots=True)
class ExpertContextHelpers:
    """Callables injected from ``agents`` without creating an import cycle."""

    invoke_context_agent: InvokeContextAgent


@dataclass(frozen=True, slots=True)
class _ExpertContextInvokeRequest:
    """Inputs for one sync Expert context agent invocation."""

    selected_agent_id: str
    dispatch: ContextAgentInvocation
    payload: ExpertQueryRequest
    dependencies: AgentRouteDependencies
    context_request: ContextAgentRequest
    helpers: ExpertContextHelpers


@dataclass(frozen=True, slots=True)
class _ExpertContextDelegateRequest:
    """Inputs for one remote Expert context acceptance."""

    selected_agent_id: str
    arguments: dict[str, Any]
    payload: ExpertQueryRequest
    dependencies: AgentRouteDependencies
    resolved_input: Any
    request_json: str
    debug: bool


__all__ = ["ExpertContextHelpers", "execute_context_expert"]


async def execute_context_expert(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    *,
    resolved_input: Any,
    channels: frozenset[str],
    helpers: ExpertContextHelpers,
) -> JSONResponse:
    """Run constrained Expert V1 selection through the shared lifecycle."""
    envelope = payload.conversation
    assert envelope is not None
    if envelope.mode != "expert":
        raise HTTPException(
            status_code=422, detail="expert context requires expert mode"
        )
    ordered_tools = _ordered_expert_tools(payload, envelope, channels)
    envelope = envelope.model_copy(update={"allowed_agent_ids": ordered_tools})
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
        resolved_attachments=resolved_input,
        dataset_description=payload.dataset_description,
    )

    async def invoke(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        return await _invoke_context_expert_agent(
            _ExpertContextInvokeRequest(
                selected_agent_id=selected_agent_id,
                dispatch=dispatch,
                payload=payload,
                dependencies=dependencies,
                context_request=context_request,
                helpers=helpers,
            )
        )

    async def delegate_async(
        selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        arguments: dict[str, Any],
    ) -> AsyncAgentAcceptance:
        return await _delegate_context_expert_async(
            _ExpertContextDelegateRequest(
                selected_agent_id=selected_agent_id,
                arguments=arguments,
                payload=payload,
                dependencies=dependencies,
                resolved_input=resolved_input,
                request_json=request_json,
                debug=context_request.debug,
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


async def _delegate_context_expert_async(
    request: _ExpertContextDelegateRequest,
) -> AsyncAgentAcceptance:
    """Prepare and accept one remote Expert agent through context V1."""
    slug = slug_for_tool(request.selected_agent_id, request.dependencies)
    prepared_arguments, attachment_context = (
        await prepare_selected_expert_arguments(
            agent=slug,
            selected_arguments=request.arguments,
            payload=request.payload,
            resolved_input=request.resolved_input,
            db_path=request.dependencies.tasks_db_path(),
        )
    )
    body, status_code = await request.dependencies.native.invoke_agent_run(
        agent=slug,
        arguments=prepared_arguments,
        dialogue_id=request.payload.dialogue_id,
        request_json=request.request_json,
        debug=request.debug,
        attachment_evidence=attachment_context.evidence,
    )
    if attachment_context.evidence is not None:
        body = redact_managed_attachment_values(
            body, attachment_context.evidence
        )
    return AsyncAgentAcceptance(body, status_code)


async def _invoke_context_expert_agent(
    request: _ExpertContextInvokeRequest,
) -> AgentOutcome:
    """Invoke one selected Expert agent through the shared context seam."""
    slug = slug_for_tool(request.selected_agent_id, request.dependencies)
    resolved = request.context_request.resolved_attachments
    if resolved is None:
        resolved = resolve_attachment_input(
            (),
            attachment_owner=(
                request.dependencies.chat.projection.current_user()
                or "anonymous"
            ),
            resolver=request.dependencies.upload.asset_resolver,
        )
    prepared_arguments, attachment_context = (
        await prepare_selected_expert_arguments(
            agent=slug,
            selected_arguments=request.dispatch.arguments,
            payload=request.payload,
            resolved_input=resolved,
            db_path=request.dependencies.tasks_db_path(),
        )
    )
    outcome = await request.helpers.invoke_context_agent(
        selected_agent_id=request.selected_agent_id,
        dispatch=replace(request.dispatch, arguments=prepared_arguments),
        request=ContextAgentRequest(
            dialogue_id=request.context_request.dialogue_id,
            request_json=request.context_request.request_json,
            debug=request.context_request.debug,
            obs_file_list=None,
            resolved_attachments=None,
            dataset_description=None,
            attachment_evidence=attachment_context.evidence,
        ),
        dependencies=request.dependencies,
    )
    if attachment_context.evidence is not None:
        return AgentOutcome(
            result=redact_managed_attachment_values(
                outcome.result, attachment_context.evidence
            ),
            assistant_summary=outcome.assistant_summary,
            context_delta=outcome.context_delta,
            context_delta_error=outcome.context_delta_error,
            status=outcome.status,
            private_stage_metadata=outcome.private_stage_metadata,
        )
    return outcome
