# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared raw tool invocation seam.

Covers invoke_tool_raw returning the unwrapped handler payload and the MCP
dispatch wrapper formatting that raw payload on top of it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from json import dumps, loads
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from mcp_server_phytomni import server
from mcp_server_phytomni.mcp.result_formatting import (
    build_tool_result_envelope,
)
from mcp_server_phytomni.runtime.execution_event_sink import (
    bind_execution_event_sink,
)

pytestmark = pytest.mark.server


@dataclass
class _EventRecorder:
    """Collect events emitted through the shared invocation boundary."""

    intents: list[Any] = field(default_factory=list)

    def emit(self, intent: Any) -> None:
        """Capture the emitted event for later assertions."""
        self.intents.append(intent)


async def test_invoke_tool_raw_returns_unwrapped_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify invoke_tool_raw validates args and returns the raw payload."""
    captured: dict[str, Any] = {}

    async def fake_handler(args: Any) -> dict[str, Any]:
        """Capture the validated model and return a raw payload."""
        captured["args"] = args
        return {"answer": args.user_query, "files": args.obs_file_list}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_handler,
    )

    payload = await server.invoke_tool_raw(
        server.PhytomniAgents.CHAT_AGENT,
        {"user_query": "hello", "obs_file_list": []},
    )

    assert isinstance(captured["args"], server.ChatAgent)
    assert payload == {"answer": "hello", "files": []}


async def test_invoke_tool_raw_emits_safe_shared_boundary_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify invoke tool raw emits safe shared boundary events."""

    async def fake_handler(_args: Any) -> dict[str, bool]:
        return {"private": True}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_handler,
    )
    recorder = _EventRecorder()

    with bind_execution_event_sink(recorder):
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT,
            {"user_query": "secret query", "obs_file_list": []},
        )

    assert [intent.kind for intent in recorder.intents] == [
        "tool.started",
        "tool.completed",
    ]
    assert recorder.intents[0].payload.tool_key == "ChatAgent"
    assert recorder.intents[1].payload.duration_ms >= 0
    serialized = repr(
        [intent.model_dump(mode="json") for intent in recorder.intents]
    )
    assert "secret query" not in serialized
    assert "private" not in serialized


async def test_invoke_tool_raw_classifies_failure_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify invoke tool raw classifies failure without exception text."""

    async def fake_handler(_args: Any) -> None:
        raise RuntimeError("credential=do-not-persist")

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_handler,
    )
    recorder = _EventRecorder()

    with (
        bind_execution_event_sink(recorder),
        pytest.raises(RuntimeError, match="do-not-persist"),
    ):
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT,
            {"user_query": "hello", "obs_file_list": []},
        )

    assert [intent.kind for intent in recorder.intents] == [
        "tool.started",
        "tool.failed",
    ]
    assert recorder.intents[-1].payload.code == "tool_execution_failed"
    assert "do-not-persist" not in repr(
        recorder.intents[-1].model_dump(mode="json")
    )


async def test_invoke_tool_raw_rejects_unknown_tool() -> None:
    """Verify invoke_tool_raw raises MCP invalid-params for unknown tool."""
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw("UnknownAgent", {})

    assert exc_info.value.error.code == INVALID_PARAMS
    assert exc_info.value.error.message == "Unknown tool: UnknownAgent"


async def test_invoke_tool_raw_rejects_invalid_arguments() -> None:
    """Verify invoke_tool_raw raises MCP invalid-params for bad args."""
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "missing required obs list"},
        )

    assert exc_info.value.error.code == INVALID_PARAMS
    assert "obs_file_list" in exc_info.value.error.message


async def test_dispatch_tool_formats_invoke_tool_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify dispatch_tool wraps invoke_tool_raw in the result envelope.

    The dispatch seam emits ``{"formatted": ..., "raw": ...}`` when
    ``PHYTOMNI_DEBUG=1`` is set; default mode strips ``raw``.
    """
    monkeypatch.setenv("PHYTOMNI_DEBUG", "1")

    async def fake_handler(args: Any) -> dict[str, Any]:
        """Return a deterministic payload for the dispatch comparison."""
        return {"echo": args.user_query}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_handler,
    )

    arguments = {"user_query": "hi", "obs_file_list": []}
    raw = await server.invoke_tool_raw(
        server.PhytomniAgents.CHAT_AGENT, arguments
    )
    wrapped = await server.dispatch_tool(
        server.PhytomniAgents.CHAT_AGENT, arguments
    )

    assert raw == {"echo": "hi"}
    assert len(wrapped) == 1
    assert wrapped[0].type == "text"
    envelope = build_tool_result_envelope(
        server.PhytomniAgents.CHAT_AGENT.value,
        raw,
        arguments=arguments,
    )
    expected = loads(
        dumps(
            {
                "formatted": asdict(envelope.formatted),
                "execution": asdict(envelope.execution),
                "raw": envelope.raw,
            }
        )
    )
    assert loads(wrapped[0].text) == expected
