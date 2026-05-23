# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP tool dispatch routing.

Covers dispatch table completeness, argument validation, handler invocation,
formatted JSON wrapping, and invalid tool or argument error behavior.
"""

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
    """Verify tool dispatch tables cover public agents."""
    expected_names = {
        server.PhytomniAgents.CHAT_AGENT.value,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        server.PhytomniAgents.DATA_AGENT.value,
        server.PhytomniAgents.ANALYST_AGENT.value,
        server.PhytomniAgents.REVIEW_AGENT.value,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        server.PhytomniAgents.DEEP_GENOME_AGENT.value,
        server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
        server.PhytomniAgents.GENE_NETWORK_AGENT.value,
        server.PhytomniAgents.GET_TASK_STATUS.value,
    }

    assert set(server.TOOL_ARGUMENT_MODELS) == expected_names
    assert set(server.TOOL_HANDLERS) == expected_names

    for tool_name in expected_names:
        assert issubclass(server.TOOL_ARGUMENT_MODELS[tool_name], BaseModel)
        assert callable(server.TOOL_HANDLERS[tool_name])


async def test_dispatch_tool_validates_calls_handler_and_wraps_json(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify dispatch validates, calls handler, and wraps envelope JSON.

    The fake ChatAgent payload has no OpenAI ``choices``, so the server
    formatter yields an empty answer with the default envelope shape.
    The handler payload survives sanitized inside ``raw`` because none
    of its keys match a credential pattern.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap dispatch handler.

    Returns:
        None after assertions pass.
    """
    captured: dict[str, Any] = {}

    async def fake_handler(args: Any) -> dict[str, Any]:
        """Capture validated handler arguments and return a payload.

        Args:
            args: Validated tool argument model passed by dispatch_tool.

        Returns:
            Minimal JSON-serializable handler payload.
        """
        captured["args"] = args
        return {
            "answer": args.user_query,
            "files": args.obs_file_list,
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_handler,
    )

    result = await server.dispatch_tool(
        server.PhytomniAgents.CHAT_AGENT,
        {"user_query": "hello", "obs_file_list": []},
    )

    assert isinstance(captured["args"], server.ChatAgent)
    assert len(result) == 1
    assert result[0].type == "text"
    assert loads(result[0].text) == {
        "formatted": {
            "answer": "",
            "follow_up_questions": [],
            "metadata": {},
            "references": [],
            "tabular": None,
            "output_dirs": [],
        },
        "raw": {
            "answer": "hello",
            "files": [],
        },
    }


async def test_dispatch_tool_rejects_unknown_tool():
    """Verify dispatch tool rejects unknown tool."""
    with pytest.raises(McpError) as exc_info:
        await server.dispatch_tool("UnknownAgent", {})

    assert exc_info.value.error.code == INVALID_PARAMS
    assert exc_info.value.error.message == "Unknown tool: UnknownAgent"


async def test_dispatch_tool_rejects_invalid_arguments():
    """Verify dispatch tool rejects invalid arguments."""
    with pytest.raises(McpError) as exc_info:
        await server.dispatch_tool(
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "missing required obs list"},
        )

    assert exc_info.value.error.code == INVALID_PARAMS
    assert "obs_file_list" in exc_info.value.error.message
