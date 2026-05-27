# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the ``invoke_tool_streamed`` MCP streaming seam.

Pins four behaviors the SSE Phase relies on:
``invoke_tool_streamed`` wraps each chunk produced by
``stream_phyto_chat_chunks`` in a frozen :class:`FormattedToolChunk`
without mutating the provider payload, returns the
``Unknown tool`` McpError before yielding for unknown tool names,
returns a sanitized validation McpError before yielding for malformed
ChatAgent arguments, and raises ``NotImplementedError`` (on first
iteration) for every other registered tool so callers see a clear
"streaming not supported" signal instead of a silent fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import FormattedToolChunk
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.server


def _chat_payload(demo_data_dir: Path) -> Dict[str, Any]:
    """Load the ChatAgent demo payload as parsed JSON."""
    return json.loads(
        (demo_data_dir / "payloads" / "chat_agent.json").read_text(
            encoding="utf-8"
        )
    )


async def _drain(
    stream: AsyncIterator[FormattedToolChunk],
) -> List[FormattedToolChunk]:
    """Collect every emitted chunk so tests can assert against the list."""
    return [chunk async for chunk in stream]


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, payloads: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Replace ``stream_phyto_chat_chunks`` with a fake yielding ``payloads``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        payloads: Provider chunk dicts the fake should yield.

    Returns:
        A list capturing the kwargs the fake was called with, so the
        single test that needs to verify the chat handler's standard
        kwargs reach the primitive can assert on it.
    """
    captured: List[Dict[str, Any]] = []

    async def fake_stream(**kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        """Capture kwargs and yield each pre-built payload in order."""
        captured.append(kwargs)
        for payload in payloads:
            yield payload

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)
    return captured


async def test_invoke_tool_streamed_wraps_chunks_in_formatted_tool_chunk(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each upstream chunk reaches the caller as a frozen FormattedToolChunk.

    Pins the wire shape the SSE shaper in Step 5.3 consumes: every
    yielded item is a :class:`FormattedToolChunk` whose ``payload``
    is the exact dict the streaming primitive produced, with unknown
    provider fields (``custom``) intact.
    """
    payloads = [
        {"id": "c1", "choices": [{"delta": {"content": "Hel"}}]},
        {
            "id": "c1",
            "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
            "custom": "kept",
        },
    ]
    captured = _patch_stream(monkeypatch, payloads)

    chunks = await _drain(
        mcp_app.invoke_tool_streamed(
            PhytomniAgents.CHAT_AGENT.value, _chat_payload(demo_data_dir)
        )
    )

    assert [c.payload for c in chunks] == payloads
    assert all(isinstance(c, FormattedToolChunk) for c in chunks)
    # The chat handler's standard kwargs reach the primitive: user_query
    # came from the demo payload, plus chat_kwargs + obs_kwargs spread.
    assert captured[0]["user_query"].startswith(
        "Explain the C3 photosynthesis"
    )
    assert "obs_file_list" in captured[0]
    assert "api_key" in captured[0]  # from chat_kwargs
    assert "access_key_id" in captured[0]  # from obs_kwargs


async def test_invoke_tool_streamed_raises_mcperror_for_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown tool name surfaces as an MCP invalid-params error.

    Pins the parity with :func:`invoke_tool_raw`: a typo'd tool name
    must not silently no-op or hang the SSE response with an empty
    iterator. The error fires on the first ``__anext__`` because
    async-generator bodies do not execute until iteration starts.
    """
    _patch_stream(monkeypatch, [])

    stream = mcp_app.invoke_tool_streamed(
        "NoSuchAgent", {"user_query": "hi", "obs_file_list": []}
    )

    with pytest.raises(McpError) as excinfo:
        await _drain(stream)

    assert "Unknown tool" in excinfo.value.error.message


async def test_invoke_tool_streamed_raises_mcperror_on_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatAgent missing ``user_query`` raises a sanitized invalid-params.

    Pins the contract that streaming validation runs through the same
    Pydantic model as the non-stream seam, so callers see consistent
    400-equivalent errors and provider payloads never leak via the
    default :func:`str(ValidationError)`.
    """
    _patch_stream(monkeypatch, [])

    stream = mcp_app.invoke_tool_streamed(
        PhytomniAgents.CHAT_AGENT.value, {"obs_file_list": []}
    )

    with pytest.raises(McpError) as excinfo:
        await _drain(stream)

    assert "Invalid arguments" in excinfo.value.error.message
    assert "user_query" in excinfo.value.error.message


@pytest.mark.parametrize(
    "tool_name",
    [
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        PhytomniAgents.REVIEW_AGENT.value,
        PhytomniAgents.BRIEF_GENE_AGENT.value,
        PhytomniAgents.GET_TASK_STATUS.value,
    ],
)
async def test_invoke_tool_streamed_raises_not_implemented_for_non_chat(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """Every non-ChatAgent registered tool raises NotImplementedError.

    Pins the v1 streaming scope: only ChatAgent is wired. Other tools
    must surface a clear "streaming not supported" signal instead of
    a silent empty stream — Step 5.4's per-model gate at the HTTP
    layer trusts this contract to translate into a 400 for non-chat
    models, and a regression that silently no-ops here would make the
    HTTP gate return 200-with-empty-body for those models.
    """
    _patch_stream(monkeypatch, [])
    # Use arguments valid against the chosen tool's schema so the
    # raise fires at the dispatch branch, not Pydantic validation.
    args_by_tool: Dict[str, Dict[str, Any]] = {
        PhytomniAgents.KNOWLEDGE_AGENT.value: {
            "user_query": "hi",
            "obs_file_list": [],
        },
        PhytomniAgents.REVIEW_AGENT.value: {
            "user_query": "hi",
            "obs_file_list": [],
        },
        PhytomniAgents.BRIEF_GENE_AGENT.value: {"user_query": "AT1G01010"},
        PhytomniAgents.GET_TASK_STATUS.value: {"task_id": "t-1"},
    }
    stream = mcp_app.invoke_tool_streamed(tool_name, args_by_tool[tool_name])

    with pytest.raises(NotImplementedError) as excinfo:
        await _drain(stream)

    assert "streaming not supported" in str(excinfo.value)
    assert tool_name in str(excinfo.value)


def test_format_tool_chunk_preserves_payload_verbatim() -> None:
    """``format_tool_chunk`` wraps the dict without copying or mutating it.

    Pins the FormattedToolChunk contract: the chunk's ``payload``
    field is the same mapping object the caller supplied, so unknown
    vendor extensions and reasoning fields survive untouched on their
    way to the SSE shaper.
    """
    payload = {"id": "c1", "vendor_extension": [1, 2, 3]}
    chunk = mcp_app.format_tool_chunk(payload)

    assert isinstance(chunk, FormattedToolChunk)
    assert chunk.payload is payload
    # Frozen dataclass — assignment routes through __setattr__ and
    # raises FrozenInstanceError. ``setattr`` is the same dynamic API
    # so static checkers stay happy without a per-line type: ignore,
    # mirroring tests/agents/test_brief_gene_pipeline_helpers.py's
    # TW-C pattern (see commit 9072103).
    with pytest.raises(AttributeError):
        setattr(chunk, "payload", {})
