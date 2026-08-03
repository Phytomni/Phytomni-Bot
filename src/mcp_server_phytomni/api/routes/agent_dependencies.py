# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dependency containers shared by primary agent route registration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi.responses import Response

from ...runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from ..schemas import AgentRunRequest
from .context_types import ContextAgentRequest
from .uploads import AgentUploadDependencies

type AgentRun = Callable[..., Awaitable[tuple[dict[str, Any], int]]]
type ChatResponse = Callable[..., Awaitable[Response]]
type QueryFlattener = Callable[[Any], str]
type ChatResolver = Callable[..., Awaitable[tuple[str, dict[str, Any]]]]


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
class AgentRouteDependencies:
    """Explicit dependencies required by the primary agent routes."""

    auth: AgentAuthDependencies
    catalog: AgentCatalogDependencies
    chat: AgentChatDependencies
    native: AgentNativeDependencies
    context: AgentContextDependencies
    upload: AgentUploadDependencies
    tasks_db_path: Callable[[], str]


@dataclass(frozen=True, slots=True)
class ContextNativePrepareRequest:
    """Inputs for preparing one context native attachment invocation."""

    agent: str
    arguments: Mapping[str, Any]
    request: ContextAgentRequest
    dependencies: AgentRouteDependencies


@dataclass(frozen=True, slots=True)
class ContextNativeExecutionRequest:
    """Inputs for executing one URL-pinned native context route."""

    agent: str
    payload: AgentRunRequest
    arguments: dict[str, Any]
    resolved_input: Any
    request_json: str
    dependencies: AgentRouteDependencies


__all__ = [
    "AgentAuthDependencies",
    "AgentCatalogDependencies",
    "AgentChatDependencies",
    "AgentChatExecutionDependencies",
    "AgentChatInputDependencies",
    "AgentChatProjectionDependencies",
    "AgentContextDependencies",
    "AgentNativeDependencies",
    "AgentRouteDependencies",
    "ContextNativeExecutionRequest",
    "ContextNativePrepareRequest",
]
