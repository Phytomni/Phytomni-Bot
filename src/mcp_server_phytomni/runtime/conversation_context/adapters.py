# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""HTTP transport adapters for bounded conversation-context turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from ...agents.data.conversation import DataConversationAdapter
from ...agents.expert import ToolSelection, ToolSelectionError
from ...agents.knowledge.conversation import (
    KnowledgeClarificationError,
    KnowledgeConversationAdapter,
)
from ...agents.review.conversation import (
    ReviewClarificationError,
    ReviewConversationAdapter,
)
from ...config.defaults import ApiConfig
from .models import BusinessContext, ContextProjection, ConversationEnvelopeV1
from .service import (
    AgentOutcome,
    AgentSelection,
    ConversationContextService,
    PreparedTurn,
)
from .store import ConversationContextStore


@dataclass(frozen=True, slots=True)
class ContextAgentInvocation:
    """Canonical public tool arguments plus private native-role history."""

    arguments: dict[str, Any]
    conversation_messages: tuple[dict[str, str], ...]
    agent_thread_id: str
    private_agent_state: dict[str, Any] = field(default_factory=dict)


SyncInvoker = Callable[
    [str, ConversationEnvelopeV1, ContextAgentInvocation],
    Awaitable[AgentOutcome],
]
AsyncInvoker = Callable[
    [str, ConversationEnvelopeV1, dict[str, Any]],
    Awaitable[dict[str, Any]],
]
RouterSelector = Callable[..., Awaitable[ToolSelection | None]]
StoreFactory = Callable[[], ConversationContextStore]


def _native_history_from_turns(
    turns: Sequence[Mapping[str, str] | object],
) -> tuple[dict[str, str], ...]:
    """Preserve the bounded chronological native-role history exactly."""
    messages: list[dict[str, str]] = []
    for turn in turns:
        role = getattr(turn, "role", None)
        content = getattr(turn, "content", None)
        if role is None and isinstance(turn, Mapping):
            role = turn.get("role")
            content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return tuple(messages)


def _native_history_from_projection(
    projection: ContextProjection,
) -> tuple[dict[str, str], ...]:
    """Return bounded native turns without adding fields to MCP schemas."""
    return _native_history_from_turns(projection.relevant_recent_turns)


def canonical_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project V1 context to current tool inputs and private role history."""
    arguments = dict(selected_arguments or {})
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=projection.agent_thread_id,
    )


def knowledge_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Knowledge V1 context into retrieval-safe private dispatch state."""
    arguments = dict(selected_arguments or {})
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    adapter = KnowledgeConversationAdapter()
    try:
        prepared = adapter.prepare(projection)
    except KnowledgeClarificationError as exc:
        return ContextAgentInvocation(
            arguments=arguments,
            conversation_messages=(),
            agent_thread_id=projection.agent_thread_id,
            private_agent_state={"clarification_message": str(exc)},
        )
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=(),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "retrieval_query": prepared["retrieval_query"],
            "answer_context": prepared["answer_context"],
            "knowledge_adapter": adapter,
        },
    )


def data_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Data V1 context into a standalone follow-up query."""
    arguments = dict(selected_arguments or {})
    arguments["locale"] = projection.locale
    adapter = DataConversationAdapter()
    prepared = adapter.prepare(projection)
    arguments["user_query"] = prepared["user_query"]
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "data_adapter": adapter,
            "dialog_id": prepared["dialog_id"],
            "rewrite_query": prepared["rewrite_query"],
        },
    )


def review_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Review context into private bounded conversation state."""
    arguments = dict(selected_arguments or {})
    checkpoint = arguments.pop("review_checkpoint", None)
    arguments.pop("review_operation", None)
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    adapter = ReviewConversationAdapter()
    try:
        prepared = adapter.prepare(
            projection,
            snapshot=checkpoint,
            allow_unresolved_section=checkpoint is None,
        )
    except ReviewClarificationError as exc:
        return ContextAgentInvocation(
            arguments=arguments,
            conversation_messages=(),
            agent_thread_id=projection.agent_thread_id,
            private_agent_state={"clarification_message": str(exc)},
        )
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "review_adapter": adapter,
            "review_operation": prepared["operation"].value,
            "review_projection": projection,
        },
    )


def native_history_from_context(
    context: BusinessContext,
) -> tuple[dict[str, str], ...]:
    """Build bounded router history from Bot-owned semantic context."""
    return _native_history_from_turns(context.recent_turns)


class ConversationContextExecutor:
    """Run one shared service with request-local HTTP transport callbacks."""

    def __init__(
        self,
        *,
        store_factory: StoreFactory,
        select_agent: RouterSelector,
        api_config_factory: Callable[[], ApiConfig] = ApiConfig,
    ) -> None:
        self._store_factory = store_factory
        self._select_agent = select_agent
        self._api_config_factory = api_config_factory
        self._service: ConversationContextService | None = None
        self._sync_invoker: ContextVar[SyncInvoker | None] = ContextVar(
            "conversation_context_sync_invoker", default=None
        )
        self._async_invoker: ContextVar[AsyncInvoker | None] = ContextVar(
            "conversation_context_async_invoker", default=None
        )
        self._selected_arguments: ContextVar[dict[str, Any] | None] = (
            ContextVar("conversation_context_selected_arguments", default=None)
        )

    def _service_for_request(self) -> ConversationContextService:
        if self._service is None:
            self._service = ConversationContextService(
                self._store_factory(),
                router=self._route,
                invoke=self._invoke,
                delegate_async=self._delegate_async,
                api_config=self._api_config_factory(),
            )
        return self._service

    async def execute(
        self,
        *,
        envelope: ConversationEnvelopeV1,
        invoke: SyncInvoker,
        delegate_async: AsyncInvoker,
    ) -> PreparedTurn:
        """Bind one transport and execute the durable context lifecycle."""
        sync_token = self._sync_invoker.set(invoke)
        async_token = self._async_invoker.set(delegate_async)
        arguments_token = self._selected_arguments.set({})
        try:
            return await self._service_for_request().execute_turn(envelope)
        finally:
            self._selected_arguments.reset(arguments_token)
            self._async_invoker.reset(async_token)
            self._sync_invoker.reset(sync_token)

    async def _route(
        self,
        user_query: str,
        allowed_agent_ids: Sequence[str],
        context: BusinessContext,
    ) -> AgentSelection:
        selection = await self._select_agent(
            user_query,
            native_history_from_context(context),
            allowed_tools=allowed_agent_ids,
            forced_tool=None,
        )
        if selection is None:
            raise ToolSelectionError("strict routing returned no selection")
        self._selected_arguments.set(dict(selection.arguments))
        return AgentSelection(selection.tool_name, "ROUTER_SELECTED")

    async def _invoke(
        self,
        selected_agent_id: str,
        envelope: ConversationEnvelopeV1,
        projection: ContextProjection,
    ) -> AgentOutcome:
        invoke = self._sync_invoker.get()
        if invoke is None:
            raise RuntimeError("context sync invoker is unavailable")
        selected_arguments = self._selected_arguments.get() or {}
        if selected_agent_id == "KnowledgeAgent":
            dispatch = knowledge_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        elif selected_agent_id == "DataAgent":
            dispatch = data_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        elif selected_agent_id == "ReviewAgent":
            dispatch = review_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        else:
            dispatch = canonical_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        return await invoke(
            selected_agent_id,
            envelope,
            dispatch,
        )

    async def _delegate_async(
        self,
        selected_agent_id: str,
        envelope: ConversationEnvelopeV1,
    ) -> dict[str, Any]:
        invoke = self._async_invoker.get()
        if invoke is None:
            raise RuntimeError("context async invoker is unavailable")
        arguments = dict(self._selected_arguments.get() or {})
        arguments.setdefault("user_query", envelope.current_message.content)
        arguments["locale"] = envelope.current_message.locale
        return await invoke(selected_agent_id, envelope, arguments)


__all__ = [
    "ContextAgentInvocation",
    "ConversationContextExecutor",
    "canonical_agent_invocation",
    "data_agent_invocation",
    "knowledge_agent_invocation",
    "native_history_from_context",
    "review_agent_invocation",
]
