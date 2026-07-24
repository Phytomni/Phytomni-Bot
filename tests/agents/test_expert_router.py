# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the in-process Expert tool router.

Mocks ``AsyncOpenAI`` and ``get_sensitive_config`` so ``select_agent_tool``
is exercised without any network or real credentials, mirroring the
client-side ``PhytomniToolRouter.route_query`` selection contract. Also
locks the tool-spec surface ``select_agent_tool`` offers to the model.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.expert import (
    ToolSelection,
    ToolSelectionError,
    select_agent_tool,
)
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.mcp.schemas import agent_openai_tool_specs

pytestmark = pytest.mark.agent


def _completion(
    *, content: str | None = None, tool_calls: list[Any] | None = None
) -> SimpleNamespace:
    """Build a fake completion shaped like ``AsyncOpenAI`` returns."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _tool_call(name: str, arguments: str) -> SimpleNamespace:
    """Build a fake OpenAI tool call with a function name + JSON args."""
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments)
    )


def _patch_openai(
    monkeypatch: pytest.MonkeyPatch,
    completion: object,
    captured: dict[str, Any] | None = None,
) -> None:
    """Patch the router's ``AsyncOpenAI`` and sensitive-config loader."""

    async def create(**kwargs: Any) -> object:
        if captured is not None:
            captured.update(kwargs)
        return completion

    def fake_async_openai(
        api_key: str, base_url: str | None
    ) -> SimpleNamespace:
        _ = (api_key, base_url)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    monkeypatch.setattr(expert_router, "AsyncOpenAI", fake_async_openai)
    monkeypatch.setattr(
        expert_router,
        "get_sensitive_config",
        lambda: SimpleNamespace(
            API_KEY=SimpleNamespace(get_secret_value=lambda: "k"),
            BASE_URL="https://example.invalid/v1",
            MODEL_ID="route-model",
        ),
    )


async def test_select_agent_tool_returns_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool call maps to a ToolSelection with parsed JSON arguments."""
    captured: dict[str, Any] = {}
    completion = _completion(
        tool_calls=[_tool_call("KnowledgeAgent", '{"user_query": "rice"}')]
    )
    _patch_openai(monkeypatch, completion, captured)

    result = await select_agent_tool("tell me about rice", history=[])

    assert isinstance(result, ToolSelection)
    assert result.tool_name == "KnowledgeAgent"
    assert result.arguments == {"user_query": "rice"}
    # The full agent tool surface is offered with auto choice.
    offered = {t["function"]["name"] for t in captured["tools"]}
    assert offered == {
        t["function"]["name"] for t in agent_openai_tool_specs()
    }
    assert captured["tool_choice"] == "auto"
    assert captured["model"] == "route-model"


async def test_strict_router_offers_allowed_tools_in_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict routing exposes only the caller's ordered allowlist."""
    captured: dict[str, Any] = {}
    _patch_openai(
        monkeypatch,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        captured,
    )

    await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
    )

    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "KnowledgeAgent",
        "ChatAgent",
    ]
    assert captured["tool_choice"] == "required"


async def test_strict_router_forces_requested_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced strict route sends the matching OpenAI tool choice."""
    captured: dict[str, Any] = {}
    _patch_openai(
        monkeypatch,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        captured,
    )

    await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
        forced_tool="ChatAgent",
    )

    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "ChatAgent"},
    }


@pytest.mark.parametrize(
    "completion",
    [
        SimpleNamespace(choices=[]),
        _completion(tool_calls=[]),
        _completion(
            tool_calls=[
                _tool_call("ChatAgent", "{}"),
                _tool_call("DataAgent", "{}"),
            ]
        ),
        _completion(tool_calls=[_tool_call("MissingAgent", "{}")]),
        _completion(tool_calls=[_tool_call("DataAgent", "{}")]),
    ],
)
async def test_strict_router_rejects_invalid_model_selection(
    monkeypatch: pytest.MonkeyPatch,
    completion: object,
) -> None:
    """Strict routing rejects missing, ambiguous, and disallowed choices."""
    _patch_openai(monkeypatch, completion)

    with pytest.raises(ToolSelectionError):
        await select_agent_tool(
            "route this",
            allowed_tools=["ChatAgent"],
        )


async def test_select_agent_tool_none_without_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A content-only completion yields no selection (chat fallback)."""
    _patch_openai(monkeypatch, _completion(content="just chatting"))
    result = await select_agent_tool("hello", history=[])
    assert result is None


async def test_select_agent_tool_none_on_empty_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-choices completion degrades to the chat fallback (no crash).

    A 200 from an OpenAI-compatible gateway with an empty ``choices`` list
    must not IndexError into a generic 500; it is treated as "no tool
    selected" so the route falls back to the chat agent.
    """
    _patch_openai(monkeypatch, SimpleNamespace(choices=[]))
    result = await select_agent_tool("hello", history=[])
    assert result is None


async def test_select_agent_tool_history_precedes_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History is sent before the current user turn for routing context."""
    captured: dict[str, Any] = {}
    _patch_openai(
        monkeypatch,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        captured,
    )
    history = [{"role": "user", "content": "earlier"}]

    await select_agent_tool("now", history=history)

    messages = captured["messages"]
    assert messages[0] == {"role": "user", "content": "earlier"}
    assert messages[-1] == {"role": "user", "content": "now"}


async def test_select_agent_tool_tolerates_non_json_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON tool arguments degrade to ``{}`` rather than crashing."""
    _patch_openai(
        monkeypatch,
        _completion(tool_calls=[_tool_call("DataAgent", "not-json")]),
    )
    result = await select_agent_tool("count genes", history=[])
    assert result is not None
    assert result.arguments == {}


def test_agent_openai_tool_specs_excludes_get_task_status() -> None:
    """The router tool universe is the ten agents, GetTaskStatus excluded."""
    specs = agent_openai_tool_specs()
    names = [spec["function"]["name"] for spec in specs]

    assert len(names) == 10
    assert "GetTaskStatus" not in names
    assert names[0] == "ChatAgent"
    for spec in specs:
        assert spec["type"] == "function"
        function = spec["function"]
        assert function["name"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
