# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared conversation-context helpers for HTTP agent routes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from ...runtime.conversation_context.adapters import ContextAgentInvocation
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    PreparedTurn,
    PrepareStatus,
)
from ...runtime.locale import SupportedLocale, current_effective_locale
from ..lifecycle_contract import (
    conversation_context_rebuild_required_error,
    conversation_context_turn_in_progress_error,
)
from .agent_dependencies import AgentRouteDependencies

__all__ = [
    "clarification_agent_run",
    "context_execution_thread_id",
    "context_response",
    "safe_native_request_json",
    "slug_for_tool",
    "stream_context_chat",
]


def clarification_agent_run(agent: str, message: str) -> dict[str, Any]:
    """Return a sync agent.run envelope for clarification-only turns."""
    formatted: dict[str, Any] = {"answer": message}
    formatted["follow_up_questions"], formatted["references"] = [], []
    return {
        "id": None,
        "object": "agent.run",
        "agent": agent,
        "status": "succeeded",
        "task_ids": [],
        "result": {"formatted": formatted},
    }


def context_execution_thread_id(
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


def context_response(
    prepared: PreparedTurn,
    envelope: ConversationEnvelopeV1,
) -> JSONResponse:
    """Return a staged terminal payload or a bounded context retry signal."""
    if prepared.status is PrepareStatus.REBUILD_REQUIRED:
        raise conversation_context_rebuild_required_error()
    if prepared.status is PrepareStatus.IN_PROGRESS:
        raise conversation_context_turn_in_progress_error()
    if prepared.result is None:
        raise HTTPException(
            status_code=500, detail="conversation context failed"
        )
    response = dict(prepared.result)
    if prepared.context_persistence_degraded:
        response["conversation_context_degraded"] = True
    elif prepared.stage is not None:
        response["conversation_context"] = {
            "schema_version": 1,
            "turn_id": envelope.turn_id,
            **prepared.stage.as_public_dict(),
        }
    status_code = 202 if response.get("status") == "running" else 200
    return JSONResponse(response, status_code=status_code)


async def stream_context_chat(
    dependencies: AgentRouteDependencies,
    payload: Any,
    attachment_arguments: Mapping[str, object],
) -> Response:
    """Stream an Instant context turn through the shared chat runtime."""
    envelope = payload.conversation
    assert envelope is not None
    user_query = envelope.current_message.content
    return await dependencies.chat.execution.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={
            "user_query": user_query,
            "locale": current_effective_locale(),
            **attachment_arguments,
        },
        payload=payload,
        user_query=user_query,
    )


def safe_native_request_json(
    *,
    dialogue_id: str | None,
    locale: SupportedLocale,
    route: str,
) -> str:
    """Serialize only bounded native request metadata."""
    return json.dumps(
        {"dialogue_id": dialogue_id, "locale": locale, "route": route},
        ensure_ascii=True,
        separators=(",", ":"),
    )


def slug_for_tool(
    tool_name: str,
    dependencies: AgentRouteDependencies,
) -> str:
    """Map a canonical selected tool back to the public native slug."""
    for slug, candidate in dependencies.catalog.agent_slug_to_tool.items():
        if candidate == tool_name:
            return slug
    raise ValueError("selected agent is unavailable")
