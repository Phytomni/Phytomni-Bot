# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the DataAgent LangGraph nodes.

Covers retrieve_node prompt assembly, rewrite_node LLM dispatch and its
empty-response defensive raise, and search_node nl2sql execution plus
its None-result defensive raise. The full agent.arun integration is out
of scope; these tests exercise each node method directly with mocked
external services.
"""

from __future__ import annotations

from typing import Any, Dict, cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.data import agent as data_agent
from mcp_server_phytomni.agents.data.agent import DataAgent, DataAgentState

pytestmark = pytest.mark.agent


def _state(**overrides: Any) -> DataAgentState:
    """Build a DataAgentState mapping with overridable keys."""
    base: Dict[str, Any] = {
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


async def test_retrieve_node_assembles_prompt_from_retrieved_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieve_node forwards retrieve hits into the rewrite prompt."""
    captured_get_prompt: Dict[str, Any] = {}

    async def fake_retrieve(**_kwargs: Any) -> Dict[str, Any]:
        return {
            "doc_list": [
                {"content": "scenario doc body", "title": "doc-1"},
            ]
        }

    def fake_get_prompt(
        _file: str, prompt_path: str, args: Dict[str, Any]
    ) -> str:
        captured_get_prompt["path"] = prompt_path
        captured_get_prompt["args"] = args
        return "RETRIEVE_PROMPT"

    monkeypatch.setattr(data_agent, "retrieve", fake_retrieve)
    monkeypatch.setattr(data_agent, "get_prompt", fake_get_prompt)

    result = await _agent().retrieve_node(_state())

    assert result == {"retrieve_prompt": "RETRIEVE_PROMPT"}
    assert captured_get_prompt["path"] == "user/database"
    assert (
        captured_get_prompt["args"]["user_query"]
        == "List orthologs of Os01g0177400"
    )


async def test_rewrite_node_returns_llm_content_as_rewrite_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rewrite_node forwards the LLM message content as rewrite_query."""

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": "SELECT gene FROM orthologs;"}}
            ]
        }

    monkeypatch.setattr(data_agent, "phyto_chat", fake_phyto_chat)

    result = await _agent().rewrite_node(_state(retrieve_prompt="USER_PROMPT"))

    assert result == {"rewrite_query": "SELECT gene FROM orthologs;"}


async def test_rewrite_node_raises_when_llm_returns_no_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty LLM response raises a sanitized INTERNAL_ERROR McpError.

    Pins the defensive branch: when phyto_chat returns a payload that
    drops the ``choices`` field, the rewrite node must reject the
    response rather than KeyError into the LangGraph runner.
    """

    async def fake_phyto_chat(**_kwargs: Any) -> Dict[str, Any]:
        return {"choices": []}

    monkeypatch.setattr(data_agent, "phyto_chat", fake_phyto_chat)

    with pytest.raises(McpError) as excinfo:
        await _agent().rewrite_node(_state(retrieve_prompt="USER_PROMPT"))

    assert "Failed to get response" in excinfo.value.error.message


async def test_search_node_executes_rewrite_query_when_rewrite_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_node uses rewrite_query when is_rewrite is True."""
    captured_requests: list[Any] = []

    async def fake_execute(request: Any) -> Dict[str, Any]:
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

    async def fake_execute(request: Any) -> Dict[str, Any]:
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
    """route_start picks the retrieve_node entry when is_rewrite is True."""
    assert _agent().route_start(_state()) == "retrieve_node"


def test_route_start_routes_directly_to_search_when_rewrite_off() -> None:
    """route_start skips retrieve and rewrite when is_rewrite is False."""
    assert _agent().route_start(_state(is_rewrite=False)) == "search_node"
