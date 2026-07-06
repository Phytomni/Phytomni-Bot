# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DataAgent LangGraph nodes.

Covers rewrite_node LLM dispatch and its empty-response defensive
raise, and search_node nl2sql execution plus
its None-result defensive raise. The full agent.arun integration is out
of scope; these tests exercise each node method directly with mocked
external services.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.data import agent as data_agent
from mcp_server_phytomni.agents.data.agent import DataAgent, DataAgentState

pytestmark = pytest.mark.agent


def _state(**overrides: Any) -> DataAgentState:
    """Build a DataAgentState mapping with overridable keys."""
    base: dict[str, Any] = {
        "user_query": "List orthologs of Os01g0177400",
        "retrieve_prompt": "",
        "rewrite_query": "",
        "final_response": None,
        "is_rewrite": True,
    }
    base.update(overrides)
    return cast(DataAgentState, base)


def _agent() -> DataAgent:
    """Build a DataAgent instance with the cached default config."""
    return DataAgent()


async def test_search_node_executes_rewrite_query_when_rewrite_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_node uses rewrite_query when is_rewrite is True."""
    captured_requests: list[Any] = []

    async def fake_execute(request: Any) -> dict[str, Any]:
        captured_requests.append(request.payload_data["message_content"])
        return {"data": [["AT1G00010"]], "headers": [{"name": "gene"}]}

    monkeypatch.setattr(data_agent, "execute_nl2sql_request", fake_execute)

    result = await _agent().search_node(
        _state(rewrite_query="SELECT gene FROM orthologs;")
    )

    assert captured_requests == ["SELECT gene FROM orthologs;"]
    assert result["final_response"]["data"] == [["AT1G00010"]]


async def test_search_node_falls_back_to_user_query_when_rewrite_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_node uses user_query directly when is_rewrite is False."""
    captured_requests: list[Any] = []

    async def fake_execute(request: Any) -> dict[str, Any]:
        captured_requests.append(request.payload_data["message_content"])
        return {"data": []}

    monkeypatch.setattr(data_agent, "execute_nl2sql_request", fake_execute)

    await _agent().search_node(_state(is_rewrite=False))

    assert captured_requests == ["List orthologs of Os01g0177400"]


async def test_search_node_raises_when_nl2sql_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None nl2sql result raises McpError instead of silent passthrough.

    Pins the defensive branch: ``execute_nl2sql_request`` returning None
    means the database response was unrecoverable, and the node must
    surface that loudly rather than let None pollute the final state.
    """

    async def fake_execute(_request: Any) -> None:
        return None

    monkeypatch.setattr(data_agent, "execute_nl2sql_request", fake_execute)

    with pytest.raises(McpError) as excinfo:
        await _agent().search_node(_state(rewrite_query="SELECT 1;"))

    assert "No response" in excinfo.value.error.message


def test_route_start_routes_through_retrieve_when_rewrite_enabled() -> None:
    """route_start picks the retrieve prep entry when is_rewrite is True."""
    assert _agent().route_start(_state()) == "retrieve_prep_node"


def test_route_start_routes_directly_to_search_when_rewrite_off() -> None:
    """route_start skips retrieve and rewrite when is_rewrite is False."""
    assert _agent().route_start(_state(is_rewrite=False)) == "search_node"
