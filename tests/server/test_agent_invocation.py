# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared raw tool invocation seam.

Covers invoke_tool_raw returning the unwrapped handler payload and the MCP
dispatch wrapper formatting that raw payload on top of it.
"""

from __future__ import annotations

from dataclasses import asdict
from json import dumps, loads
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from mcp_server_phytomni import server
from mcp_server_phytomni.mcp.result_formatting import format_tool_result

pytestmark = pytest.mark.server


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
    """Verify dispatch_tool is invoke_tool_raw plus formatting + JSON."""

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
    expected = loads(
        dumps(
            asdict(
                format_tool_result(
                    server.PhytomniAgents.CHAT_AGENT.value,
                    raw,
                    arguments=arguments,
                )
            )
        )
    )
    assert loads(wrapped[0].text) == expected
