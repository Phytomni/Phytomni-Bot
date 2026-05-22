# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for invoke_tool_raw pydantic ValidationError handling.

The raw dispatch seam must catch pydantic ValidationError specifically
and emit a sanitized INVALID_PARAMS message that names the offending
field but never echoes the caller-supplied input value.
"""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from mcp_server_phytomni import server

pytestmark = pytest.mark.server


async def test_invoke_tool_raw_rejects_missing_required():
    """Missing required field surfaces field name and validation tag.

    Returns:
        None after the McpError carries INVALID_PARAMS plus a message
        that contains the offending field name and the sanitized prefix.
    """
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "what is photosynthesis"},
        )

    err = exc_info.value.error
    assert err.code == INVALID_PARAMS
    assert "obs_file_list" in err.message
    assert "Invalid arguments for ChatAgent" in err.message


async def test_invoke_tool_raw_rejects_wrong_type():
    """Wrong type for a required field surfaces the field path.

    Returns:
        None after the McpError carries INVALID_PARAMS and references
        the offending field path in the sanitized summary.
    """
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "x", "obs_file_list": 42},
        )

    err = exc_info.value.error
    assert err.code == INVALID_PARAMS
    assert "obs_file_list" in err.message


async def test_invoke_tool_raw_sanitizes_input_value():
    """The sanitized message must never echo caller-supplied values.

    Returns:
        None after the McpError message excludes the sentinel input
        value and the pydantic 'input_value' marker that the default
        str(exc) representation would otherwise embed.
    """
    sentinel = "VERY_SENSITIVE_TOKEN_4F2C9A"
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw(
            server.PhytomniAgents.CHAT_AGENT.value,
            {"user_query": "x", "obs_file_list": sentinel},
        )

    err = exc_info.value.error
    assert err.code == INVALID_PARAMS
    assert sentinel not in err.message
    assert "input_value" not in err.message


async def test_invoke_tool_raw_rejects_unknown_tool():
    """Unknown tool name surfaces an explicit INVALID_PARAMS message.

    Returns:
        None after the McpError preserves the legacy unknown-tool
        message contract so HTTP and stdio clients see one shape.
    """
    with pytest.raises(McpError) as exc_info:
        await server.invoke_tool_raw("UnknownAgent", {})

    err = exc_info.value.error
    assert err.code == INVALID_PARAMS
    assert err.message == "Unknown tool: UnknownAgent"
