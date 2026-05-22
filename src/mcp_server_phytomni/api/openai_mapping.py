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
    "tool_accepts_obs",
    "tool_accepts_resolve_gene_id",
    "flatten_messages",
    "to_chat_completion",
]

# Chat-like agents exposed through /v1/chat/completions.
MODEL_TO_TOOL = {
    "phyto-chat": "ChatAgent",
    "phyto-knowledge": "KnowledgeAgent",
    "phyto-review": "ReviewAgent",
    "phyto-brief-gene": "BriefGeneAgent",
}

# Tools whose request schema carries an obs_file_list field. BriefGene
# only takes a single gene/transcript id, so it rejects document lists.
_OBS_CAPABLE_TOOLS = {"ChatAgent", "KnowledgeAgent", "ReviewAgent"}

# Tools that support HTTP-side resolve_gene_id LLM preprocessing. Only
# BriefGene benefits because it requires a single canonical id and is
# the agent advertised standalone to external clients.
_RESOLVE_GENE_ID_CAPABLE_TOOLS = {"BriefGeneAgent"}


def tool_for_model(model: str) -> Optional[str]:
    """Return the MCP tool name for an OpenAI-style model id."""
    return MODEL_TO_TOOL.get(model)


def tool_accepts_obs(tool_name: str) -> bool:
    """Return True when the tool's schema accepts obs_file_list."""
    return tool_name in _OBS_CAPABLE_TOOLS


def tool_accepts_resolve_gene_id(tool_name: str) -> bool:
    """Return True when the tool supports resolve_gene_id preprocessing."""
    return tool_name in _RESOLVE_GENE_ID_CAPABLE_TOOLS


def flatten_messages(
    messages: Sequence[Any],
) -> str:
    """Flatten OpenAI chat messages into a single user query.

    A lone user message is returned verbatim so identifier-driven tools
    (BriefGene takes a gene/transcript id) see the raw content; any
    multi-message conversation keeps ``role: content`` blocks to
    preserve turn context.

    Args:
        messages: Sequence of objects exposing ``role`` and ``content``.

    Returns:
        The user content verbatim for a single user message, or the
        conversation rendered as ``role: content`` blocks otherwise.

    Raises:
        ValueError: When no user message is present.
    """
    msgs = list(messages)
    if not any(getattr(m, "role", None) == "user" for m in msgs):
        raise ValueError("messages must include a user message")
    if len(msgs) == 1 and getattr(msgs[0], "role", None) == "user":
        return str(msgs[0].content)
    return "\n\n".join(f"{m.role}: {m.content}" for m in msgs)


def to_chat_completion(
    result: Any, model: str, extra_keys: Sequence[str] = ()
) -> dict[str, Any]:
    """Shape a wrapper result into an OpenAI ChatCompletion dict.

    A result that already looks like a ChatCompletion is passed through
    with required metadata ensured. Any other payload is wrapped into a
    single assistant message; selected extra keys are surfaced at the
    top level so clients keep follow_up_questions / references / metadata.

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
