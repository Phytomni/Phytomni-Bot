# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers for the OpenAI-compatible chat surface.

Functions: tool_for_model, flatten_messages, to_chat_completion.
``to_chat_completion`` takes the formatted display block and the
sanitized raw handler payload separately so the OpenAI-shaped response
keeps provider fields (``reasoning_content``, ``tool_calls``,
``usage``, ``finish_reason``, ``system_fingerprint``, unknown
extensions) on the choices / top level while also surfacing the full
envelope under top-level ``formatted`` and ``raw`` keys for clients
that want the structured display view or the full sanitized payload.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional, Sequence

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
    formatted: Mapping[str, Any], raw: Any, model: str
) -> dict[str, Any]:
    """Shape an envelope into an OpenAI ChatCompletion + envelope dict.

    When ``raw`` already looks like a ChatCompletion (has ``choices``),
    its provider-returned fields (``usage`` / ``system_fingerprint`` /
    ``finish_reason`` / per-choice ``reasoning_content`` /
    ``tool_calls`` / ``refusal`` / unknown extensions) survive at the
    top level. Otherwise a single assistant message is synthesized from
    ``formatted["answer"]``. Both branches attach the full
    ``formatted`` and ``raw`` blocks at the top level so clients can
    pick the display view or the full sanitized payload.

    Args:
        formatted: ``asdict(FormattedToolResult)`` carrying the
            normalized display fields.
        raw: Sanitized handler payload returned by the agent path.
        model: The requested model id, echoed back.

    Returns:
        A JSON-serializable ChatCompletion-shaped dict with top-level
        ``formatted`` and ``raw`` envelope blocks.
    """
    base: dict[str, Any] = {
        "id": IdFactory().new_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
    }
    if isinstance(raw, dict) and raw.get("choices"):
        completion = {**raw, **base, "model": model}
        if "id" in raw and raw["id"]:
            completion["id"] = raw["id"]
    else:
        content = ""
        if isinstance(formatted, Mapping):
            content = str(formatted.get("answer") or "")
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
    completion["formatted"] = (
        dict(formatted) if isinstance(formatted, Mapping) else formatted
    )
    completion["raw"] = raw
    return completion
