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
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..mcp.result_formatting import AguiEvent
from ..storage.path_policy import IdFactory
from .agent_capabilities import (
    get_agent_slug_for_tool,
    get_attachment_capability,
)

__all__ = [
    "MODEL_TO_TOOL",
    "tool_for_model",
    "tool_accepts_obs",
    "tool_accepts_resolve_gene_id",
    "tool_accepts_resolve_to_id",
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

# Tools that support HTTP-side resolve_gene_id LLM preprocessing.
# DeepGenome / DigitalDesign share BriefGene's canonical gene id
# namespace and therefore reuse the same resolver-flag wiring.
_RESOLVE_GENE_ID_CAPABLE_TOOLS = {
    "BriefGeneAgent",
    "DeepGenomeAgent",
    "DigitalDesignAgent",
}

# Tools that support HTTP-side resolve_to_id LLM preprocessing.
# GeneNetwork takes a Trait Ontology id (e.g. ``TO:0000207``) whose
# closed-set catalog ships at ``config/to_ontology.json``; the
# resolver injects that catalog into the LLM prompt and validates
# the proposed id against it before returning.
_RESOLVE_TO_ID_CAPABLE_TOOLS = {"GeneNetworkAgent"}

# Tools that support SSE streaming via ``invoke_tool_streamed``.
# ChatAgent token-streams provider deltas; KnowledgeAgent / ReviewAgent
# / BriefGeneAgent drive their compiled graphs through the
# ``_stream_graph_agent`` primitive, emitting stage ``StepStarted``
# frames then a one-shot terminal answer + citations. DataAgent stays
# out of this set: it carries no chat-completions model alias and thus
# no SSE entry point. Keep this set aligned with the seam's
# implemented branches — adding a model here without also wiring its
# streaming primitive in ``invoke_tool_streamed`` would surface as a
# 500 (``NotImplementedError``) at request time.
_STREAM_CAPABLE_TOOLS = {
    "ChatAgent",
    "KnowledgeAgent",
    "ReviewAgent",
    "BriefGeneAgent",
}


def tool_for_model(model: str) -> str | None:
    """Return the MCP tool name for an OpenAI-style model id."""
    return MODEL_TO_TOOL.get(model)


def tool_accepts_obs(tool_name: str) -> bool:
    """Return True when the tool's schema accepts obs_file_list."""
    slug = get_agent_slug_for_tool(tool_name)
    if slug is None:
        return False
    return get_attachment_capability(slug).document_context is not None


def tool_accepts_resolve_gene_id(tool_name: str) -> bool:
    """Return True when the tool supports resolve_gene_id preprocessing."""
    return tool_name in _RESOLVE_GENE_ID_CAPABLE_TOOLS


def tool_accepts_resolve_to_id(tool_name: str) -> bool:
    """Return True when the tool supports resolve_to_id preprocessing."""
    return tool_name in _RESOLVE_TO_ID_CAPABLE_TOOLS


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
    stream: AsyncIterator[AguiEvent], model: str
) -> AsyncIterator[str]:
    """Shape an ``AguiEvent`` stream into AG-UI SSE frames.

    Each upstream event becomes one ``event: <Type>\\n`` line followed
    by a ``data: {...}\\n\\n`` line carrying the event's JSON payload.
    After the upstream iterator drains, a final ``data: [DONE]\\n\\n``
    signals stream end so clients close their EventSource without
    timing out.

    AG-UI frames do not carry an OpenAI ``model`` field — the
    requested model id is not echoed onto any frame, per spec §3.2.
    ``model`` stays a parameter because callers pass it uniformly
    alongside the non-streaming shaper, but this shaper does not
    project it anywhere.

    The event's ``data`` mapping already embeds a redundant ``"type"``
    key (see :class:`AguiEvent`), so clients can parse the event kind
    without relying on the ``event:`` line.

    Args:
        stream: Async iterator of ``AguiEvent`` produced by
            ``invoke_tool_streamed``.
        model: The requested model id; unused by AG-UI framing but
            kept in the signature for parity with ``to_chat_completion``.

    Yields:
        One ``event: <Type>\\ndata: {...}\\n\\n`` frame per upstream
        event, then a terminal ``data: [DONE]\\n\\n``.
    """
    del model
    async for event in stream:
        payload = dict(event.data)
        yield (
            f"event: {event.type}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        )
    yield "data: [DONE]\n\n"
