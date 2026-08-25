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

from ...agents.research.input_contracts import ResearchInputFailure
from ...config.defaults import ServerConfig
from ...runtime.conversation_context.adapters import ContextAgentInvocation
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    AgentOutcome,
    AsyncAgentAcceptance,
)
from ...runtime.locale import current_effective_locale
from ...runtime.research_input_store import ResearchInputStore
from ..agent_capabilities import (
    agent_uses_user_query,
    filter_tools_for_attachment_channels,
)
from ..app_support import resolve_http_locale
from ..attachments import redact_managed_attachment_values
from ..lifecycle_contract import SafeApiError
from ..research_fingerprint import (
    research_client_fingerprint_for_http_input,
)
from ..research_input import (
    ResearchHttpAdmissionInput,
    lookup_research_admission,
    parse_idempotency_identity,
)
from ..resolvers import apply_expert_structured_resolver_flags
from ..schemas import ExpertQueryRequest
from .agent_dependencies import AgentRouteDependencies
from .attachment_inputs import (
    attachment_not_supported_error,
    build_expert_research_admission,
    expert_attachment_channels,
    expert_attachment_requirement,
    filter_expert_attachment_candidates,
    prepare_selected_expert_arguments,
    resolve_attachment_input,
    restrict_expert_candidates_for_research,
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
    research_admission: _ResearchContextAdmission | None = None


@dataclass(frozen=True, slots=True)
class _ResearchContextAdmission:
    """Research-only immutable admission facts for one Expert turn."""

    request: Any
    bundle: Any


@dataclass(frozen=True, slots=True)
class _ResearchReplayAliasRequest:
    """Caller-owned facts needed to bind a replay header alias."""

    replay: Any
    payload: ExpertQueryRequest
    envelope: ConversationEnvelopeV1
    owner: str
    idempotency_key: str | None
    db_path: str


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
    idempotency_key: str | None = None,
    execution_id: str | None = None,
    selected_arguments: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Run constrained Expert V1 selection through the shared lifecycle."""
    envelope = payload.conversation
    assert envelope is not None
    if envelope.mode != "expert":
        raise HTTPException(
            status_code=422, detail="expert context requires expert mode"
        )
    resolve_http_locale(
        explicit=envelope.current_message.locale,
        accept_language=None,
        latest_user_query=envelope.current_message.content,
    )
    replay = await inspect_context_replay(
        executor=dependencies.context.executor,
        envelope=envelope,
        selection_failure_detail=(
            "router did not resolve one permitted agent"
        ),
    )
    if replay is not None:
        _attach_research_replay_alias(
            _ResearchReplayAliasRequest(
                replay,
                payload,
                envelope,
                attachment_owner,
                idempotency_key,
                dependencies.tasks_db_path(),
            )
        )
        return context_response(replay, envelope)
    effective_forced_tool = envelope.requested_agent_id or payload.forced_tool
    payload = payload.model_copy(
        update={
            "user_query": envelope.current_message.content,
            "allowed_tools": list(
                restrict_expert_candidates_for_research(
                    envelope.current_message.content,
                    ServerConfig().BUCKET_NAME,
                    payload.allowed_tools,
                    effective_forced_tool,
                )
            ),
        }
    )
    payload, envelope, prepared_by_tool = _prepare_expert_context_inputs(
        payload, dependencies, attachment_owner, envelope, idempotency_key
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
        execution_id=execution_id,
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
            selected_arguments=selected_arguments,
            selection_failure_detail=(
                "router did not resolve one permitted agent"
            ),
        )
    )
    return context_response(prepared, envelope)


def _attach_research_replay_alias(
    request_input: _ResearchReplayAliasRequest,
) -> None:
    """Atomically attach a replay alias without reopening Research inputs."""
    stage = request_input.replay.stage
    if (
        request_input.idempotency_key is None
        or stage is None
        or stage.selected_agent_id != "InSilicoResearchAgent"
    ):
        return
    request = ResearchHttpAdmissionInput(
        owner=request_input.owner,
        idempotency_key=request_input.idempotency_key,
        conversation=request_input.envelope,
        original_query=request_input.envelope.current_message.content,
        managed_asset_ids=tuple(
            getattr(item, "asset_id", None)
            for item in request_input.payload.attachments
        ),
        locale=request_input.envelope.current_message.locale,
        interop_mode="off",
        interop_targets=(),
        route_source="expert",
    )
    try:
        identity = parse_idempotency_identity(
            request.idempotency_key, request.conversation
        )
        fingerprint = research_client_fingerprint_for_http_input(
            request, identity.canonical_digest
        )
        lookup_research_admission(
            owner=request.owner,
            identity=identity,
            client_fingerprint=fingerprint,
            store=ResearchInputStore(request_input.db_path),
        )
    except ResearchInputFailure as exc:
        raise _research_replay_error(exc) from exc


def _research_replay_error(exc: ResearchInputFailure) -> SafeApiError:
    """Project a classified replay failure without exposing store details."""
    return SafeApiError(
        status_code=exc.http_status_hint,
        code=exc.code,
        message="Research input resolution failed.",
        stage=exc.stage,
        retryable=exc.retryable,
    )


def _prepare_expert_context_inputs(
    payload: ExpertQueryRequest,
    dependencies: AgentRouteDependencies,
    attachment_owner: str,
    envelope: ConversationEnvelopeV1,
    idempotency_key: str | None,
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
            payload,
            dependencies,
            ordered_tools,
            resolved_input,
            idempotency_key,
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
    idempotency_key: str | None,
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
            research_admission=(
                _ResearchContextAdmission(
                    build_expert_research_admission(
                        payload,
                        resolved_input,
                        idempotency_key=idempotency_key,
                        route_source="expert",
                    ),
                    resolved_input.bundle,
                )
                if tool_name == "InSilicoResearchAgent"
                else None
            ),
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
        prepared.setdefault("goal_description", payload.user_query)
        prepared.pop("user_query", None)
    elif agent_uses_user_query(agent):
        prepared.setdefault("user_query", payload.user_query)
        prepared.pop("goal_description", None)
    prepared["locale"] = current_effective_locale()
    apply_expert_structured_resolver_flags(
        agent=agent,
        arguments=prepared,
        user_query=payload.user_query,
    )
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
    options: dict[str, Any] = {
        "agent": slug,
        "arguments": prepared_arguments,
        "dialogue_id": request.context_request.dialogue_id,
        "request_json": request.context_request.request_json,
        "debug": request.context_request.debug,
        "attachment_evidence": request.attachments.evidence,
        "execution_id": request.context_request.execution_id,
    }
    research_admission = request.attachments.research_admission
    if research_admission is not None:
        options["research_http_input"] = research_admission.request
        options["research_attachment_bundle"] = research_admission.bundle
    body, status_code = await request.dependencies.native.invoke_agent_run(
        **options
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
            execution_id=request.context_request.execution_id,
        ),
        dependencies=request.dependencies,
        research_admission=request.attachments.research_admission,
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
