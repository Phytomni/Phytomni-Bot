# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for A2A v1 message-to-MCP request mapping."""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import (
    ContentTypeNotSupportedError,
    InvalidParamsError,
    Message,
    Part,
    Role,
)
from google.protobuf import json_format

from mcp_server_phytomni.agents.expert.router import ToolSelection
from mcp_server_phytomni.api.a2a.messages import map_a2a_message

pytestmark = pytest.mark.unit


def _data_part(payload: dict[str, Any]) -> Part:
    part = Part()
    json_format.ParseDict({"data": payload}, part)
    return part


def _message(*parts: Part, metadata: dict[str, Any] | None = None) -> Message:
    message = Message(role=Role.ROLE_USER, parts=list(parts))
    if metadata:
        for key, value in metadata.items():
            message.metadata[key] = value
    return message


async def _choose_chat(_text: str) -> ToolSelection:
    return ToolSelection("ChatAgent", {"user_query": "routed"})


async def test_skill_id_routes_text_and_data_without_mutating_message() -> (
    None
):
    """Request metadata selects a tool and merges text/data.

    The merge is deterministic.
    """
    message = _message(
        Part(text="Explain rice flowering"),
        _data_part({"obs_file_list": []}),
    )

    mapped = await map_a2a_message(
        message,
        request_metadata={"skill_id": "KnowledgeAgent"},
    )

    assert mapped.tool_name == "KnowledgeAgent"
    assert mapped.arguments == {
        "user_query": "Explain rice flowering",
        "obs_file_list": [],
    }
    assert message.metadata == {}


async def test_message_metadata_does_not_route_skill() -> None:
    """Only request-level metadata may select an A2A skill."""
    mapped = await map_a2a_message(
        _message(Part(text="hello"), metadata={"skill_id": "DataAgent"}),
        select_agent=_choose_chat,
    )

    assert mapped.tool_name == "ChatAgent"
    assert mapped.arguments["user_query"] == "routed"


async def test_missing_skill_uses_expert_selector_and_merges_data() -> None:
    """Expert routing receives text while structured fields reach dispatch."""
    mapped = await map_a2a_message(
        _message(
            Part(text="find evidence"), _data_part({"obs_file_list": []})
        ),
        select_agent=_choose_chat,
    )

    assert mapped.tool_name == "ChatAgent"
    assert mapped.arguments == {"user_query": "routed", "obs_file_list": []}


async def test_expert_router_without_selection_falls_back_to_chat() -> None:
    """A router no-op keeps the existing Expert fallback behavior."""

    async def no_selection(_text: str) -> None:
        return None

    mapped = await map_a2a_message(
        _message(Part(text="general question")),
        select_agent=no_selection,
    )

    assert mapped == type(mapped)(
        tool_name="ChatAgent",
        arguments={"user_query": "general question", "obs_file_list": []},
    )


async def test_unknown_skill_is_an_invalid_params_error() -> None:
    """Unknown catalog ids fail as JSON-RPC invalid parameters."""
    with pytest.raises(InvalidParamsError, match="unknown A2A skill"):
        await map_a2a_message(
            _message(Part(text="hello")),
            request_metadata={"skill_id": "MissingAgent"},
        )


@pytest.mark.parametrize("part", [Part(raw=b"abc"), Part(url="https://x")])
async def test_raw_and_url_parts_are_rejected(part: Part) -> None:
    """The v1 raw and URL oneof variants are outside Phase 1 scope."""
    with pytest.raises(ContentTypeNotSupportedError):
        await map_a2a_message(
            _message(part),
            request_metadata={"skill_id": "ChatAgent"},
        )


async def test_data_part_must_be_a_json_object() -> None:
    """Scalar protobuf Values cannot become tool argument mappings."""
    part = Part()
    json_format.ParseDict({"data": "not an object"}, part)
    with pytest.raises(ContentTypeNotSupportedError, match="JSON object"):
        await map_a2a_message(
            _message(part),
            request_metadata={"skill_id": "ChatAgent"},
        )


async def test_conflicting_data_parts_are_rejected() -> None:
    """Different values for one structured key never silently overwrite."""
    with pytest.raises(InvalidParamsError, match="conflicting A2A data"):
        await map_a2a_message(
            _message(
                _data_part({"obs_file_list": ["a"]}),
                _data_part({"obs_file_list": ["b"]}),
            ),
            request_metadata={"skill_id": "KnowledgeAgent"},
        )


async def test_conflicting_text_and_data_are_rejected() -> None:
    """Text and structured values for one tool field must agree."""
    with pytest.raises(InvalidParamsError, match="conflicting A2A text"):
        await map_a2a_message(
            _message(
                Part(text="from text"),
                _data_part({"user_query": "from data"}),
            ),
            request_metadata={"skill_id": "ChatAgent"},
        )


async def test_text_is_required_when_expert_routing_has_no_data_query() -> (
    None
):
    """Expert routing cannot infer a query from unrelated structured data."""
    with pytest.raises(InvalidParamsError, match="requires text"):
        await map_a2a_message(
            _message(_data_part({"obs_file_list": []})),
            select_agent=_choose_chat,
        )
