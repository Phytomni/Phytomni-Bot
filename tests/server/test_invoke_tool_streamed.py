# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the ``invoke_tool_streamed`` MCP streaming seam.

Pins the AG-UI event sequence (``RunStarted``..``RunFinished``)
wrapping the chat token stream, early McpError responses for unknown
tools and malformed ChatAgent arguments, and ``NotImplementedError``
for registered tools that do not support streaming.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    FormattedToolChunk,
    format_tool_chunk,
)
from mcp_server_phytomni.mcp.schemas import PhytomniAgents

pytestmark = pytest.mark.server


def _chat_payload(demo_data_dir: Path) -> dict[str, Any]:
    """Load the ChatAgent demo payload as parsed JSON."""
    return json.loads(
        (demo_data_dir / "payloads" / "chat_agent.json").read_text(
            encoding="utf-8"
        )
    )


async def _drain(stream: AsyncIterator[AguiEvent]) -> list[AguiEvent]:
    """Collect every emitted event so tests can assert against the list."""
    return [event async for event in stream]


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, payloads: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace ``stream_phyto_chat_chunks`` with a fake yielding ``payloads``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        payloads: Provider chunk dicts the fake should yield.

    Returns:
        A list capturing the kwargs the fake was called with, so the
        single test that needs to verify the chat handler's standard
        kwargs reach the primitive can assert on it.
    """
    captured: list[dict[str, Any]] = []

    async def fake_stream(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Capture kwargs and yield each pre-built payload in order."""
        captured.append(kwargs)
        for payload in payloads:
            yield payload

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)
    return captured


async def test_chat_stream_emits_six_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatAgent streaming yields RunStarted..RunFinished around deltas."""

    async def fake_stream(**_kwargs):
        yield {"choices": [{"delta": {"content": "Hel"}}]}
        yield {
            "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]
        }

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", fake_stream)

    events = [
        e
        async for e in mcp_app.invoke_tool_streamed(
            "ChatAgent",
            {"user_query": "hi", "obs_file_list": []},
            run_id="run-x",
            dialogue_id="dlg-x",
        )
    ]
    types = [e.type for e in events]
    assert types == [
        "RunStarted",
        "TextMessageStart",
        "TextMessageContent",
        "TextMessageContent",
        "TextMessageEnd",
        "RunFinished",
    ]
    assert events[0].data["run_id"] == "run-x"
    assert events[2].data["delta"] == "Hel"
    assert events[-1].data["run_id"] == "run-x"


async def test_invoke_tool_streamed_reaches_primitive_with_standard_kwargs(
    demo_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat handler's standard kwargs reach the streaming primitive.

    Pins the wire shape the SSE shaper in ``api/openai_mapping.py``
    consumes: content deltas surface as ``TextMessageContent`` events
    in provider order, and the chat handler's standard kwargs
    (``chat_kwargs`` + ``obs_kwargs`` spread) reach
    ``stream_phyto_chat_chunks`` unchanged.
    """
    # Literals chosen distinct from tests/agents/test_chat_agent_streaming.py
    # so the two test files do not register as an R0801 duplicate block —
    # both tests only need a small chunk sequence with a unique vendor
    # field; their payload content is otherwise incidental.
    payloads = [
        {"id": "seam-c1", "choices": [{"delta": {"content": "A"}}]},
        {
            "id": "seam-c1",
            "choices": [{"delta": {"content": "B"}, "finish_reason": "stop"}],
            "vendor_seam_tag": "kept",
        },
    ]
    captured = _patch_stream(monkeypatch, payloads)

    events = await _drain(
        mcp_app.invoke_tool_streamed(
            PhytomniAgents.CHAT_AGENT.value,
            _chat_payload(demo_data_dir),
            run_id="run-1",
            dialogue_id=None,
        )
    )

    deltas = [
        e.data["delta"] for e in events if e.type == "TextMessageContent"
    ]
    assert deltas == ["A", "B"]
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
        "NoSuchAgent",
        {"user_query": "hi", "obs_file_list": []},
        run_id="run-1",
        dialogue_id=None,
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
        PhytomniAgents.CHAT_AGENT.value,
        {"obs_file_list": []},
        run_id="run-1",
        dialogue_id=None,
    )

    with pytest.raises(McpError) as excinfo:
        await _drain(stream)

    assert "Invalid arguments" in excinfo.value.error.message
    assert "user_query" in excinfo.value.error.message


@pytest.mark.parametrize(
    "tool_name",
    [
        PhytomniAgents.BRIEF_GENE_AGENT.value,
        PhytomniAgents.GET_TASK_STATUS.value,
    ],
)
async def test_invoke_tool_streamed_raises_not_implemented_for_non_chat(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """A registered non-streaming tool raises NotImplementedError.

    Pins the current streaming scope: ChatAgent token-streams and
    KnowledgeAgent / ReviewAgent drive their compiled graphs through
    ``_stream_graph_agent``; every other registered tool (BriefGene,
    GetTaskStatus) must surface a clear "streaming not supported"
    signal instead of a silent empty stream — the per-model gate at
    the HTTP layer trusts this contract to translate into a 400 for
    those models, and a regression that silently no-ops here would
    make the HTTP gate return 200-with-empty-body for them.
    """
    _patch_stream(monkeypatch, [])
    # Use arguments valid against the chosen tool's schema so the
    # raise fires at the dispatch branch, not Pydantic validation.
    args_by_tool: dict[str, dict[str, Any]] = {
        PhytomniAgents.BRIEF_GENE_AGENT.value: {"user_query": "AT1G01010"},
        PhytomniAgents.GET_TASK_STATUS.value: {"task_id": "t-1"},
    }
    stream = mcp_app.invoke_tool_streamed(
        tool_name,
        args_by_tool[tool_name],
        run_id="run-1",
        dialogue_id=None,
    )

    with pytest.raises(NotImplementedError) as excinfo:
        await _drain(stream)

    assert "streaming not supported" in str(excinfo.value)
    assert tool_name in str(excinfo.value)


async def test_chat_stream_emits_run_error_on_midstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raise after opening yields RunError, not an unhandled crash."""

    async def boom(**_kwargs):
        yield {"choices": [{"delta": {"content": "Hi"}}]}
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="upstream 502 at https://secret.internal",
            )
        )

    monkeypatch.setattr(mcp_app, "stream_phyto_chat_chunks", boom)

    events = [
        e
        async for e in mcp_app.invoke_tool_streamed(
            "ChatAgent",
            {"user_query": "x", "obs_file_list": []},
            run_id="run-e",
            dialogue_id=None,
        )
    ]
    assert events[-1].type == "RunError"
    assert events[-1].data["code"] == "agent_execution_failed"
    assert "secret.internal" not in events[-1].data["message"]
    assert "RunFinished" not in [e.type for e in events]


def test_format_tool_chunk_preserves_payload_verbatim() -> None:
    """``format_tool_chunk`` wraps the dict without copying or mutating it.

    ``invoke_tool_streamed`` no longer calls ``format_tool_chunk``
    (its yields are now ``AguiEvent`` frames), but the primitive
    itself is still a valid public helper on
    ``mcp.result_formatting``, so its wrapping contract stays pinned
    directly against that module.
    """
    payload = {"id": "c1", "vendor_extension": [1, 2, 3]}
    chunk = format_tool_chunk(payload)

    assert isinstance(chunk, FormattedToolChunk)
    assert chunk.payload is payload
    # Frozen dataclass — assignment routes through __setattr__ and
    # raises FrozenInstanceError. ``setattr`` is the same dynamic API
    # so static checkers stay happy without a per-line type: ignore,
    # mirroring tests/agents/test_brief_gene_pipeline_helpers.py's
    # TW-C pattern (see commit 9072103).
    with pytest.raises(AttributeError):
        setattr(chunk, "payload", {})
