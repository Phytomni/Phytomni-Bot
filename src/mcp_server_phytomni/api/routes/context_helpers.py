# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared conversation-context helpers for HTTP agent routes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    PreparedTurn,
    PrepareStatus,
)
from ...runtime.locale import SupportedLocale, current_effective_locale
from ..lifecycle_contract import SafeApiError
from .agent_dependencies import AgentRouteDependencies

__all__ = [
    "context_response",
    "safe_native_request_json",
    "slug_for_tool",
    "stream_context_chat",
]


def context_response(
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
    if prepared.context_persistence_degraded:
        response["conversation_context_degraded"] = True
    elif prepared.stage is not None:
        response["conversation_context"] = {
            "schema_version": 1,
            "turn_id": envelope.turn_id,
            **asdict(prepared.stage),
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
