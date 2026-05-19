# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers for the OpenAI-compatible chat surface.

Functions: tool_for_model, flatten_messages, to_chat_completion.

These are side-effect-free so the request/response shaping can be unit
tested without a server or network.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from ..storage.path_policy import IdFactory

__all__ = [
    "MODEL_TO_TOOL",
    "tool_for_model",
    "flatten_messages",
    "to_chat_completion",
]

# Chat-like agents exposed through /v1/chat/completions. Knowledge,
# Review, and BriefGene are wired in a later layer.
MODEL_TO_TOOL = {
    "phyto-chat": "ChatAgent",
}


def tool_for_model(model: str) -> Optional[str]:
    """Return the MCP tool name for an OpenAI-style model id."""
    return MODEL_TO_TOOL.get(model)


def flatten_messages(
    messages: Sequence[Any],
) -> str:
    """Flatten OpenAI chat messages into a single user query.

    Args:
        messages: Sequence of objects exposing ``role`` and ``content``.

    Returns:
        The conversation rendered as ``role: content`` blocks.

    Raises:
        ValueError: When no user message is present.
    """
    if not any(getattr(m, "role", None) == "user" for m in messages):
        raise ValueError("messages must include a user message")
    return "\n\n".join(f"{m.role}: {m.content}" for m in messages)


def to_chat_completion(
    result: Any, model: str, extra_keys: Sequence[str] = ()
) -> dict[str, Any]:
    """Shape a wrapper result into an OpenAI ChatCompletion dict.

    A result that already looks like a ChatCompletion is passed through
    with required metadata ensured. Any other payload is wrapped into a
    single assistant message; selected extra keys are surfaced at the
    top level so clients keep doc_list / follow_up_questions.

    Args:
        result: The raw wrapper payload.
        model: The requested model id, echoed back.
        extra_keys: Top-level result keys to copy onto the response.

    Returns:
        A JSON-serializable ChatCompletion-shaped dict.
    """
    base: dict[str, Any] = {
        "id": IdFactory().new_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
    }
    if isinstance(result, dict) and result.get("choices"):
        completion = {**result, **base, "model": model}
        if "id" in result and result["id"]:
            completion["id"] = result["id"]
        return completion

    content = ""
    if isinstance(result, dict):
        content = str(result.get("answer") or result.get("content") or "")
    elif result is not None:
        content = str(result)
    completion = {
        **base,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if isinstance(result, dict):
        for key in extra_keys:
            if key in result:
                completion[key] = result[key]
    return completion
