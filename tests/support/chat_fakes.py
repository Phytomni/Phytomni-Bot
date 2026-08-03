# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-only ChatCompletion handler fakes for HTTP tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.runtime.conversation_context.models import (
    RoleTaggedTurn,
)

__all__ = [
    "ChatCompletionOptions",
    "chat_completion_payload",
    "install_chat_handler",
    "misplaced_reasoning_message",
    "recent_knowledge_context",
    "recent_knowledge_turns",
]


def recent_knowledge_context() -> str:
    """Return the bounded recent-turn context used by knowledge fixtures."""
    return (
        "[recent turn 1]\nuser: Tell me about rice gene OsDREB1.\n\n"
        "[recent turn 2]\nassistant: OsDREB1 improves drought "
        "tolerance [1]."
    )


def recent_knowledge_turns() -> list[RoleTaggedTurn]:
    """Return the matching user/assistant turns for context fixtures."""
    return [
        RoleTaggedTurn(
            role="user",
            content="Tell me about rice gene OsDREB1.",
        ),
        RoleTaggedTurn(
            role="assistant",
            content="OsDREB1 improves drought tolerance [1].",
        ),
    ]


@dataclass(frozen=True, slots=True)
class ChatCompletionOptions:
    """Optional provider fields for one canonical completion fixture."""

    follow_up_questions: list[str] | None = None
    message_fields: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    system_fingerprint: str | None = None


def chat_completion_payload(
    completion_id: str,
    content: str,
    options: ChatCompletionOptions | None = None,
) -> dict[str, Any]:
    """Build a canonical provider completion for HTTP route fixtures."""
    options = options or ChatCompletionOptions()
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if options.follow_up_questions is not None:
        message["follow_up_questions"] = options.follow_up_questions
    if options.message_fields:
        message.update(options.message_fields)
    result: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
    }
    if options.usage is not None:
        result["usage"] = dict(options.usage)
    if options.system_fingerprint is not None:
        result["system_fingerprint"] = options.system_fingerprint
    return result


def misplaced_reasoning_message() -> dict[str, str]:
    """Return the shared provider message used by repair-path tests."""
    return {
        "role": "assistant",
        "content": "",
        "reasoning_content": (
            "<think>identify chlorophyll</think>" "Leaves capture light."
        ),
    }


def install_chat_handler(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    content: str = "photosynthesis converts light",
    follow_up_questions: list[str] | None = None,
) -> None:
    """Register one deterministic ChatAgent completion handler."""

    async def fake(args: Any) -> dict[str, Any]:
        """Capture the query and return a canonical completion."""
        captured["user_query"] = args.user_query
        captured["obs_file_list"] = getattr(args, "obs_file_list", None)
        return chat_completion_payload(
            "chatcmpl-canned",
            content,
            ChatCompletionOptions(follow_up_questions=follow_up_questions),
        )

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
