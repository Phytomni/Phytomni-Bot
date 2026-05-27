# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OpenAI-compatible chat mapping helpers.

Defines model lookup, message flattening, completion envelope shaping,
and SSE chunk shaping. The shapers keep OpenAI canonical fields in
place while attaching Phytomni formatted and raw payloads where the API
contract expects them.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Mapping, Optional, Sequence

from ..mcp.result_formatting import FormattedToolChunk
from ..storage.path_policy import IdFactory

__all__ = [
    "MODEL_TO_TOOL",
    "tool_for_model",
    "tool_accepts_obs",
    "tool_accepts_resolve_gene_id",
    "tool_accepts_stream",
    "flatten_messages",
    "to_chat_completion",
    "to_chat_completion_chunks",
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

# Tools that support SSE streaming via ``invoke_tool_streamed``. v1
# wires only ChatAgent — the other chat-like models (knowledge /
# review / brief-gene) either need full-document retrieval state or
# return a structured single answer, both of which would surface as
# a single trailing chunk rather than a token stream. Adding a model
# here without also implementing its streaming primitive in
# ``invoke_tool_streamed`` would surface as a 500
# (``NotImplementedError``) at request time; keep this set narrow.
_STREAM_CAPABLE_TOOLS = {"ChatAgent"}


def tool_for_model(model: str) -> Optional[str]:
    """Return the MCP tool name for an OpenAI-style model id."""
    return MODEL_TO_TOOL.get(model)


def tool_accepts_obs(tool_name: str) -> bool:
    """Return True when the tool's schema accepts obs_file_list."""
    return tool_name in _OBS_CAPABLE_TOOLS


def tool_accepts_resolve_gene_id(tool_name: str) -> bool:
    """Return True when the tool supports resolve_gene_id preprocessing."""
    return tool_name in _RESOLVE_GENE_ID_CAPABLE_TOOLS


def tool_accepts_stream(tool_name: str) -> bool:
    """Return True when the tool supports SSE streaming.

    Used by the ``/v1/chat/completions`` route to gate ``stream=true``
    before calling :func:`invoke_tool_streamed`; a False return yields
    a clean per-model 400 instead of relying on the seam's
    ``NotImplementedError`` to surface deep in the stack.
    """
    return tool_name in _STREAM_CAPABLE_TOOLS


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


async def to_chat_completion_chunks(
    stream: AsyncIterator[FormattedToolChunk], model: str
) -> AsyncIterator[str]:
    """Shape a ``FormattedToolChunk`` stream into OpenAI SSE event lines.

    Each upstream chunk becomes one ``data: {...}\\n\\n`` line carrying
    the canonical OpenAI ``chat.completion.chunk`` JSON. After the
    upstream iterator drains, a final ``data: [DONE]\\n\\n`` signals
    stream end so clients close their EventSource without timing out.

    The shaper makes two minimal projections on each payload:

    1. ``object`` is filled with ``"chat.completion.chunk"`` when the
       provider omitted it, so OpenAI-compatible clients see the
       canonical event type on every line.
    1. ``model`` is overridden with the requested model id, mirroring
       :func:`to_chat_completion`'s consistency rule — the request
       model name surfaces to the client even if the upstream
       provider returned a different routing slug.

    Unknown vendor fields (``reasoning_content``, ``tool_calls``,
    extensions) survive untouched on every line. The shaper is a
    pure projection — it does not mutate the input
    :class:`FormattedToolChunk` (the chunk is frozen anyway) and
    does not buffer; emits each line as it pulls one chunk.

    Args:
        stream: Async iterator of ``FormattedToolChunk`` produced by
            ``invoke_tool_streamed``.
        model: The requested model id, echoed into each line's
            ``model`` field.

    Yields:
        One ``data: {...}\\n\\n`` line per upstream chunk, then a
        terminal ``data: [DONE]\\n\\n``.
    """
    async for chunk in stream:
        payload = dict(chunk.payload)
        payload.setdefault("object", "chat.completion.chunk")
        payload["model"] = model
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
