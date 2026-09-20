# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Detached execution-command projection into existing Agent seams."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from fastapi import FastAPI

from ..runtime.attachment_assets import ResolvedAttachmentBundle
from ..runtime.conversation_context.models import ConversationEnvelopeV1
from ..runtime.execution_entrypoint_v2 import (
    CanonicalReservationIdentity,
    bind_canonical_reservation_identity,
)
from ..runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from ..runtime.execution_runtime_contracts import ExecutionCommand
from ..runtime.locale import bind_effective_locale
from ..runtime.request_context import reset_request_var
from .lifecycle_contract import SafeApiError
from .schemas import ExpertQueryRequest

type AppAttributeResolver = Callable[[str], Any]
type UserResolver = Callable[[], str | None]
type TasksPathResolver = Callable[[], Any]
type ExecutionCommandInvoke = Callable[..., Awaitable[Any]]
type RoutedReservationBinder = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ExecutionDispatchServices:
    """App-scoped compatibility seams used by detached dispatch."""

    app: FastAPI
    app_attr: AppAttributeResolver
    current_user: UserResolver
    tasks_db_path: TasksPathResolver
    routed_reservation_binder: RoutedReservationBinder


@dataclass(frozen=True, slots=True)
class _ContextRouteValues:
    """Private route fields removed from durable Agent arguments."""

    query: Any
    allowed_tools: Any
    dialogue_id: Any
    forced_tool: Any
    locale: Any


@dataclass(slots=True)
class _ConversationRuntime:
    """Validated context envelope and existing MCP conversation inputs."""

    value: ConversationEnvelopeV1 | None
    messages: tuple[dict[str, str], ...]
    thread_id: str | None
    private_state: dict[str, Any] | None
    route: _ContextRouteValues | None


@dataclass(frozen=True, slots=True)
class _DispatchIdentity:
    """Canonical Agent and attachment owner for detached invocation."""

    attachment_owner: str
    agent_slug: str


@dataclass(slots=True)
class _DispatchRequest:
    """Parsed private command state for one detached invocation."""

    tool: str
    runtime_arguments: dict[str, Any]
    private_arguments: dict[str, Any]
    attachments: Any
    identity: _DispatchIdentity
    conversation: _ConversationRuntime
    options: dict[str, Any]


def execution_conversation_runtime(
    value: object,
) -> tuple[tuple[dict[str, str], ...], str | None, dict[str, Any] | None]:
    """Validate private Web context and project existing MCP seams."""
    if value is None:
        return (), None, None
    envelope = ConversationEnvelopeV1.model_validate(value)
    messages = tuple(
        {"role": item.role, "content": str(item.content or item.summary)}
        for item in envelope.history_delta
        if item.role in {"user", "assistant"}
        and bool(item.content or item.summary)
    )
    return (
        messages,
        str(envelope.conversation_key),
        {"conversation_envelope": envelope.model_dump(mode="json")},
    )


def execution_conversation_dispatch_kind(value: object) -> str:
    """Select the existing context entrypoint without changing Agent logic."""
    envelope = ConversationEnvelopeV1.model_validate(value)
    return "expert" if envelope.mode == "expert" else "native"


def _pop_context_route_values(
    arguments: dict[str, Any], *, enabled: bool
) -> _ContextRouteValues | None:
    """Remove context-only routing fields before Agent schema validation."""
    if not enabled:
        return None
    return _ContextRouteValues(
        query=arguments.pop("__query", None),
        allowed_tools=arguments.pop("__allowed_tools", None),
        dialogue_id=arguments.pop("__dialogue_id", None),
        forced_tool=arguments.pop("__forced_tool", None),
        locale=arguments.pop("locale", "en-US"),
    )


def _dispatch_request(
    tool: str,
    arguments: dict[str, Any],
    options: dict[str, Any],
    services: ExecutionDispatchServices,
) -> _DispatchRequest:
    """Parse trusted private fields from one durable command."""
    private_arguments = dict(arguments)
    attachments = private_arguments.pop("__attachments", [])
    conversation_value = private_arguments.pop("__conversation", None)
    conversation = (
        None
        if conversation_value is None
        else ConversationEnvelopeV1.model_validate(conversation_value)
    )
    messages, thread_id, private_state = execution_conversation_runtime(
        conversation
    )
    attachment_owner = private_arguments.pop(
        "__attachment_owner", services.current_user() or ""
    )
    private_agent_slug = private_arguments.pop("__agent_slug", None)
    claimed_agent_slug = options.pop("agent_slug", None)
    if (
        private_agent_slug is not None
        and private_agent_slug != claimed_agent_slug
    ):
        raise ValueError("invalid_canonical_execution_identity")
    agent_slug = (
        claimed_agent_slug if isinstance(claimed_agent_slug, str) else ""
    )
    return _DispatchRequest(
        tool=tool,
        runtime_arguments=arguments,
        private_arguments=private_arguments,
        attachments=attachments,
        identity=_DispatchIdentity(attachment_owner, agent_slug),
        conversation=_ConversationRuntime(
            value=conversation,
            messages=messages,
            thread_id=thread_id,
            private_state=private_state,
            route=_pop_context_route_values(
                private_arguments,
                enabled=(conversation is not None or tool == "ExpertRouter"),
            ),
        ),
        options=options,
    )


def _context_identity(
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
) -> CanonicalReservationIdentity:
    """Validate and construct the selected context execution identity."""
    execution_id = request.options.get("execution_id")
    fingerprint_version = request.options.get("fingerprint_version")
    fingerprint = request.options.get("fingerprint")
    if (
        not request.identity.agent_slug
        or not isinstance(execution_id, str)
        or not isinstance(fingerprint_version, int)
        or not isinstance(fingerprint, str)
    ):
        raise ValueError("invalid_canonical_execution_identity")
    return CanonicalReservationIdentity(
        owner=services.current_user() or "anonymous",
        execution_id=execution_id,
        fingerprint_version=fingerprint_version,
        fingerprint=fingerprint,
        command=ExecutionCommand(
            agent_slug=request.identity.agent_slug,
            arguments=request.runtime_arguments,
        ),
    )


def _record_context_stage(
    body: dict[str, Any],
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
) -> None:
    """Persist the context projection before acknowledging dispatch."""
    stage = body.get("conversation_context")
    if not isinstance(stage, dict):
        return
    execution_id = request.options["execution_id"]
    recorded = SQLiteExecutionReservationRepository(
        str(services.tasks_db_path())
    ).record_context_stage(
        owner=services.current_user() or "anonymous",
        execution_id=execution_id,
        stage=stage,
    )
    if not recorded:
        raise SafeApiError(
            status_code=503,
            code="context_stage_projection_pending",
            message="execution context projection is pending",
            stage="execution_context",
            retryable=True,
        )
    services.app_attr("_LOGGER").info(
        "execution context stage recorded execution_id=%s",
        execution_id,
    )


async def _execute_context_expert(
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
) -> dict[str, Any]:
    """Dispatch an Expert envelope through its context lifecycle."""
    route = request.conversation.route
    assert route is not None
    if not isinstance(route.query, str) or not isinstance(
        route.allowed_tools, list
    ):
        raise ValueError("invalid_context_execution_command")
    payload = ExpertQueryRequest(
        user_query=route.query,
        attachments=request.attachments,
        owner_subject=request.identity.attachment_owner,
        dialogue_id=(
            route.dialogue_id if isinstance(route.dialogue_id, str) else None
        ),
        allowed_tools=route.allowed_tools,
        forced_tool=(
            route.forced_tool if isinstance(route.forced_tool, str) else None
        ),
        locale=route.locale if route.locale in {"en-US", "zh-CN"} else "en-US",
        conversation=request.conversation.value,
    )
    expert_context = import_module(
        "mcp_server_phytomni.api.routes.expert_context"
    )
    agent_routes = import_module("mcp_server_phytomni.api.routes.agents")
    identity = _context_identity(request, services)
    with bind_canonical_reservation_identity(identity):
        response = await expert_context.execute_context_expert(
            payload,
            services.app.state.agent_route_dependencies,
            attachment_owner=request.identity.attachment_owner,
            idempotency_key=identity.execution_id,
            execution_id=identity.execution_id,
            selected_arguments=request.private_arguments,
            helpers=expert_context.ExpertContextHelpers(
                invoke_context_agent=getattr(
                    agent_routes, "_invoke_context_agent"
                )
            ),
        )
    body = json.loads(response.body)
    _record_context_stage(body, request, services)
    return body


def _resolve_attachment_input(
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
) -> Any:
    """Resolve managed attachments only when the command carries them."""
    attachment_inputs = import_module(
        "mcp_server_phytomni.api.routes.attachment_inputs"
    )
    if not request.attachments:
        return attachment_inputs.ResolvedAttachmentInput(
            attachment_owner=request.identity.attachment_owner,
            bundle=ResolvedAttachmentBundle(assets=()),
        )
    resolver_factory = getattr(
        services.app.state,
        "research_input_asset_resolver_factory",
        None,
    )
    if resolver_factory is None:
        raise RuntimeError("asset_resolver_unavailable")
    return attachment_inputs.resolve_attachment_input(
        request.attachments,
        attachment_owner=request.identity.attachment_owner,
        resolver=resolver_factory,
    )


def _merge_attachment_evidence(
    request: _DispatchRequest, evidence: Any
) -> None:
    """Expose managed evidence only through the private Agent state."""
    if evidence is None:
        return
    request.conversation.private_state = {
        **(request.conversation.private_state or {}),
        "managed_attachment_evidence": evidence,
    }


async def _execute_expert_router(
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
    resolved: Any,
) -> Any:
    """Select and invoke one V0 Expert command below its reservation."""
    route = request.conversation.route
    if (
        request.conversation.value is not None
        or route is None
        or not isinstance(route.query, str)
        or not isinstance(route.allowed_tools, list)
    ):
        raise ValueError("invalid_expert_router_command")
    payload = ExpertQueryRequest(
        user_query=route.query,
        history=[dict(item) for item in request.conversation.messages],
        dialogue_id=(
            route.dialogue_id if isinstance(route.dialogue_id, str) else None
        ),
        allowed_tools=route.allowed_tools,
        locale=route.locale if route.locale in {"en-US", "zh-CN"} else "en-US",
        conversation=request.conversation.value,
    )
    locale_token = bind_effective_locale(payload.locale or "en-US")
    attachment_inputs = import_module(
        "mcp_server_phytomni.api.routes.attachment_inputs"
    )
    try:
        selection, selected_slug = await services.app_attr(
            "_select_expert_routing"
        )(payload)
        selected_arguments, prepared = (
            attachment_inputs.prepare_selected_expert_arguments(
                agent=selected_slug,
                selected_arguments=selection.arguments,
                payload=payload,
                resolved_input=resolved,
                db_path=str(services.tasks_db_path()),
            )
        )
    finally:
        reset_request_var(locale_token)
    _merge_attachment_evidence(request, prepared.evidence)
    with services.routed_reservation_binder(
        db_path=str(services.tasks_db_path()),
        owner=services.current_user() or "anonymous",
        execution_id=request.options["execution_id"],
        command=ExecutionCommand(
            agent_slug=selected_slug,
            arguments=selected_arguments,
        ),
    ):
        return await import_module(
            "mcp_server_phytomni.mcp.app"
        ).invoke_tool_enveloped(
            selection.tool_name,
            selected_arguments,
            conversation_messages=request.conversation.messages,
            agent_thread_id=request.conversation.thread_id,
            private_agent_state=request.conversation.private_state,
            **request.options,
        )


async def _execute_native(
    request: _DispatchRequest,
    services: ExecutionDispatchServices,
    resolved: Any,
) -> Any:
    """Project native attachment state and invoke the selected tool."""
    if request.attachments:
        attachment_inputs = import_module(
            "mcp_server_phytomni.api.routes.attachment_inputs"
        )
        arguments, prepared = (
            attachment_inputs.prepare_native_attachment_arguments(
                agent=request.identity.agent_slug,
                arguments=request.private_arguments,
                resolved_input=resolved,
                db_path=str(services.tasks_db_path()),
            )
        )
        request.private_arguments = arguments
        _merge_attachment_evidence(request, prepared.evidence)
    return await import_module(
        "mcp_server_phytomni.mcp.app"
    ).invoke_tool_enveloped(
        request.tool,
        request.private_arguments,
        runtime_arguments=request.runtime_arguments,
        conversation_messages=request.conversation.messages,
        agent_thread_id=request.conversation.thread_id,
        private_agent_state=request.conversation.private_state,
        **request.options,
    )


def build_execution_command_invoker(
    services: ExecutionDispatchServices,
) -> ExecutionCommandInvoke:
    """Build the detached command callback used by the process dispatcher."""

    async def invoke(
        tool: str,
        arguments: dict[str, Any],
        **options: Any,
    ) -> Any:
        request = _dispatch_request(tool, arguments, options, services)
        if (
            request.conversation.value is not None
            and request.conversation.route is not None
            and execution_conversation_dispatch_kind(
                request.conversation.value
            )
            == "expert"
        ):
            return await _execute_context_expert(request, services)
        resolved = _resolve_attachment_input(request, services)
        if request.tool == "ExpertRouter":
            return await _execute_expert_router(request, services, resolved)
        return await _execute_native(request, services, resolved)

    return invoke


__all__ = [
    "ExecutionDispatchServices",
    "build_execution_command_invoker",
    "execution_conversation_dispatch_kind",
    "execution_conversation_runtime",
]
