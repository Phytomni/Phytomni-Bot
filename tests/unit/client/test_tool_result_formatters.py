# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for MCP client result deserialization.

The MCP server formats tool output upstream; the client only
reconstructs FormattedToolResult and keeps a backward-compatible
format_tool_result shim that ignores legacy keyword arguments.
"""

import pytest

from mcp_client_phytomni.tool_result_formatters import (
    FormattedToolResult,
    format_tool_result,
    parse_formatted_result,
)

pytestmark = pytest.mark.unit


def test_parse_formatted_result_round_trips_server_payload() -> None:
    """Verify a server asdict payload rebuilds typed dataclass fields."""
    payload = {
        "answer": "Evidence appears in [1].",
        "follow_up_questions": ["Next question?"],
        "metadata": {"task_id": "t-1"},
        "references": [{"file_id": "doc-a", "title": "Paper A"}],
    }

    result = parse_formatted_result(payload)

    assert isinstance(result, FormattedToolResult)
    assert result.answer == "Evidence appears in [1]."
    assert result.follow_up_questions == ("Next question?",)
    assert result.metadata == {"task_id": "t-1"}
    assert result.references == ({"file_id": "doc-a", "title": "Paper A"},)


def test_format_tool_result_shim_ignores_legacy_kwargs() -> None:
    """Verify the shim parses the payload and drops legacy arguments."""
    payload = {"answer": "ok", "follow_up_questions": [], "metadata": {}}

    result = format_tool_result(
        "KnowledgeAgent",
        payload,
        arguments={"user_query": "q"},
        field_mapper=lambda headers: headers,
        reference_resolver=lambda _file_id: None,
    )

    assert result == parse_formatted_result(payload)
    assert result.answer == "ok"
