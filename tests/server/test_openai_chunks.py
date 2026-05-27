# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``to_chat_completion_chunks`` SSE shaper.

Pins event framing, unknown vendor-field preservation, requested-model
override, and terminal ``[DONE]`` behavior for zero, one, or many
upstream chunks.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List

import pytest

from mcp_server_phytomni.api.openai_mapping import to_chat_completion_chunks
from mcp_server_phytomni.mcp.result_formatting import (
    FormattedToolChunk,
    format_tool_chunk,
)

pytestmark = pytest.mark.server


async def _async_iter(
    chunks: List[FormattedToolChunk],
) -> AsyncIterator[FormattedToolChunk]:
    """Yield each pre-built chunk so tests can feed lists into the shaper."""
    for chunk in chunks:
        yield chunk


def _parse_event(line: str) -> dict:
    """Strip ``data: `` and trailing ``\\n\\n``, parse the JSON body."""
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    return json.loads(line[len("data: ") : -len("\n\n")])


async def test_chunks_emit_data_lines_and_done_terminator() -> None:
    """Two upstream chunks produce two ``data:`` lines plus ``[DONE]``.

    Pins the SSE wire format: each event line is exactly
    ``data: {...}\\n\\n``, and the terminator is the literal
    ``data: [DONE]\\n\\n`` line so OpenAI-compatible clients close
    their EventSource on the first match instead of waiting for an
    idle timeout.
    """
    chunks = [
        format_tool_chunk(
            {"id": "c1", "choices": [{"delta": {"content": "Hel"}}]}
        ),
        format_tool_chunk(
            {
                "id": "c1",
                "choices": [
                    {"delta": {"content": "lo"}, "finish_reason": "stop"}
                ],
            }
        ),
    ]

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(chunks), model="phyto-chat"
        )
    ]

    assert len(lines) == 3
    assert lines[-1] == "data: [DONE]\n\n"
    parsed_first = _parse_event(lines[0])
    assert parsed_first["id"] == "c1"
    assert parsed_first["choices"][0]["delta"]["content"] == "Hel"


async def test_to_chat_completion_chunks_preserves_unknown_vendor_fields() -> (
    None
):
    """Unknown provider extensions survive in every emitted line.

    Pins the forward-compatibility promise: when a provider returns
    fields the codebase has not yet learned about (``reasoning_content``,
    vendor extensions), the shaper passes them through untouched so
    downstream clients can opt-in to provider-specific extras without
    a server-side migration.
    """
    chunks = [
        format_tool_chunk(
            {
                "id": "c1",
                "choices": [
                    {
                        "delta": {
                            "content": "Hi",
                            "reasoning_content": "internal trace",
                        }
                    }
                ],
                "vendor_extension": {"latency_ms": 12, "shard": "west"},
            }
        ),
    ]

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(chunks), model="phyto-chat"
        )
    ]

    parsed = _parse_event(lines[0])
    assert parsed["vendor_extension"] == {"latency_ms": 12, "shard": "west"}
    delta = parsed["choices"][0]["delta"]
    assert delta["reasoning_content"] == "internal trace"


async def test_chunks_override_model_with_requested_id() -> None:
    """``model`` echoes the caller's request, not whatever the provider sent.

    Pins consistency with :func:`to_chat_completion`: clients see the
    model name they asked for, even when the upstream provider
    returned an internal routing slug (``deepseek-reasoner-v3`` vs
    public ``phyto-chat``).
    """
    chunks = [
        format_tool_chunk(
            {
                "id": "c1",
                "model": "deepseek-reasoner-v3",
                "choices": [{"delta": {"content": "Hi"}}],
            }
        ),
    ]

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(chunks), model="phyto-chat"
        )
    ]

    assert _parse_event(lines[0])["model"] == "phyto-chat"


async def test_to_chat_completion_chunks_fills_object_marker_when_absent() -> (
    None
):
    """An upstream chunk without ``object`` gets ``chat.completion.chunk``.

    Pins the canonical event-type fill-in: OpenAI-compatible SDKs
    discriminate stream events by ``object``, and some providers omit
    it for non-final chunks. ``setdefault`` ensures every emitted line
    carries the canonical marker without overriding a provider value
    that was already present.
    """
    payload_without_object = {
        "id": "c1",
        "choices": [{"delta": {"content": "Hi"}}],
    }
    payload_with_object = {
        "id": "c2",
        "object": "vendor.preview.chunk",
        "choices": [{"delta": {"content": "Bye"}}],
    }
    chunks = [
        format_tool_chunk(payload_without_object),
        format_tool_chunk(payload_with_object),
    ]

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(chunks), model="phyto-chat"
        )
    ]

    assert _parse_event(lines[0])["object"] == "chat.completion.chunk"
    # ``setdefault`` does not override a provider-supplied object kind.
    assert _parse_event(lines[1])["object"] == "vendor.preview.chunk"


async def test_to_chat_completion_chunks_empty_stream_still_emits_done() -> (
    None
):
    """Zero upstream chunks still yield exactly one ``[DONE]`` terminator.

    Pins the corner case where the provider returns an immediately-
    exhausted stream (e.g. a filter blocked the prompt before any
    tokens were generated). Without the terminator, clients would
    hang on their EventSource until the read timeout fired.
    """
    chunks: List[FormattedToolChunk] = []

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(chunks), model="phyto-chat"
        )
    ]

    assert lines == ["data: [DONE]\n\n"]
