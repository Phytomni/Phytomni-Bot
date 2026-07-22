# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Data-only ChatCompletion handler fakes for HTTP tests."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni import server


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
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if follow_up_questions is not None:
            message["follow_up_questions"] = follow_up_questions
        return {
            "id": "chatcmpl-canned",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
