# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP tool dispatch routing."""

from __future__ import annotations

from json import loads
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS
from pydantic import BaseModel

from mcp_server_phytomni import server

pytestmark = pytest.mark.server


def test_tool_dispatch_tables_cover_public_agents():
    expected_names = {
        server.PhytomniAgents.CHATAGENT.value,
        server.PhytomniAgents.KNOWLEDGEAGENT.value,
        server.PhytomniAgents.DATAAGENT.value,
        server.PhytomniAgents.ANALYSTAGENT.value,
        server.PhytomniAgents.REVIEWAGENT.value,
        server.PhytomniAgents.BRIEFGENEAGENT.value,
        server.PhytomniAgents.DEEPGENOMEAGENT.value,
        server.PhytomniAgents.INSILICORESEARCHAGENT.value,
        server.PhytomniAgents.DIGITALDESIGNAGENT.value,
        server.PhytomniAgents.GENENETWORKAGENT.value,
    }

    assert set(server.TOOL_ARGUMENT_MODELS) == expected_names
    assert set(server.TOOL_HANDLERS) == expected_names

    for tool_name in expected_names:
        assert issubclass(server.TOOL_ARGUMENT_MODELS[tool_name], BaseModel)
        assert callable(server.TOOL_HANDLERS[tool_name])


async def test_dispatch_tool_validates_calls_handler_and_wraps_json(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    async def fake_handler(args: Any) -> dict[str, Any]:
        captured["args"] = args
        return {
            "answer": args.user_query,
            "files": args.obs_file_list,
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHATAGENT.value,
        fake_handler,
    )

    result = await server.dispatch_tool(
        server.PhytomniAgents.CHATAGENT,
        {"user_query": "hello", "obs_file_list": []},
    )

    assert isinstance(captured["args"], server.ChatAgent)
    assert len(result) == 1
    assert result[0].type == "text"
    assert loads(result[0].text) == {"answer": "hello", "files": []}


async def test_dispatch_tool_rejects_unknown_tool():
    with pytest.raises(McpError) as exc_info:
        await server.dispatch_tool("UnknownAgent", {})

    assert exc_info.value.error.code == INVALID_PARAMS
    assert exc_info.value.error.message == "Unknown tool: UnknownAgent"


async def test_dispatch_tool_rejects_invalid_arguments():
    with pytest.raises(McpError) as exc_info:
        await server.dispatch_tool(
            server.PhytomniAgents.CHATAGENT.value,
            {"user_query": "missing required obs list"},
        )

    assert exc_info.value.error.code == INVALID_PARAMS
    assert "obs_file_list" in exc_info.value.error.message
