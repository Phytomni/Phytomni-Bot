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

import httpx
import pytest
from openai import BadRequestError

from mcp_server_phytomni.agents.expert import (
    ExpertRoutingContractError,
    ExpertRoutingOptions,
    ToolSelection,
    ToolSelectionError,
    select_agent_tool,
    select_expert_tool,
)
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.mcp.schemas import agent_openai_tool_specs
from tests.support.expert_router_fakes import patch_expert_router

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


async def test_select_agent_tool_returns_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool call maps to a ToolSelection with parsed JSON arguments."""
    captured: dict[str, Any] = {}
    completion = _completion(
        tool_calls=[_tool_call("KnowledgeAgent", '{"user_query": "rice"}')]
    )
    patch_expert_router(monkeypatch, expert_router, completion, captured)

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
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        captured,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
    )

    assert result == ToolSelection("ChatAgent", {})
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
    patch_expert_router(
        monkeypatch,
        expert_router,
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


async def test_select_expert_tool_scopes_prompt_and_order() -> None:
    """The strict seam scopes the prompt and preserves caller tool order."""
    captured: dict[str, Any] = {}

    async def fake_completion(**kwargs: Any) -> object:
        captured.update(kwargs)
        return _completion(tool_calls=[_tool_call("ChatAgent", "{}")])

    result = await select_expert_tool(
        user_query="route this",
        history=[{"role": "user", "content": "earlier"}],
        options=ExpertRoutingOptions(
            allowed_tools=("KnowledgeAgent", "ChatAgent"),
            forced_tool=None,
            locale="zh-CN",
            completion=fake_completion,
        ),
    )

    assert result == ToolSelection("ChatAgent", {})
    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "KnowledgeAgent",
        "ChatAgent",
    ]
    assert captured["tool_choice"] == "required"
    assert captured["messages"][0]["role"] == "system"
    assert "Simplified Chinese" in captured["messages"][0]["content"]
    assert captured["messages"][1] == {
        "role": "user",
        "content": "earlier",
    }


@pytest.mark.parametrize(
    "completion",
    [
        _completion(tool_calls=[SimpleNamespace(function=SimpleNamespace())]),
        _completion(tool_calls=[_tool_call("ChatAgent", "not-json")]),
        _completion(tool_calls=[_tool_call("ChatAgent", "[]")]),
    ],
)
async def test_select_expert_tool_rejects_malformed_arguments(
    completion: object,
) -> None:
    """Strict selection rejects malformed arguments as contract errors."""

    async def fake_completion(**_kwargs: Any) -> object:
        return completion

    with pytest.raises(ExpertRoutingContractError):
        await select_expert_tool(
            user_query="route this",
            history=[],
            options=ExpertRoutingOptions(
                allowed_tools=("ChatAgent",),
                forced_tool=None,
                locale="en-US",
                completion=fake_completion,
            ),
        )


_FORCED_TOOL_ARGUMENTS = (
    ("ChatAgent", '{"user_query": "q", "obs_file_list": []}'),
    ("KnowledgeAgent", '{"user_query": "q", "obs_file_list": []}'),
    ("DataAgent", '{"user_query": "q"}'),
    (
        "AnalystAgent",
        '{"goal_description": "q", "data_list": {}, "obs_file_list": []}',
    ),
    ("ReviewAgent", '{"user_query": "q", "obs_file_list": []}'),
    ("BriefGeneAgent", '{"user_query": "AT1G01010"}'),
    (
        "DeepGenomeAgent",
        '{"species_code": "ath", "gene_id": "AT1G01010"}',
    ),
    (
        "InSilicoResearchAgent",
        '{"user_query": "q", "data_list": {}, "obs_file_list": []}',
    ),
    (
        "DigitalDesignAgent",
        '{"species_code": "ath", "gene_id": "AT1G01010", "obs_file_list": []}',
    ),
    (
        "GeneNetworkAgent",
        '{"species_code": "ath", "to_id": "TO:0000001", "obs_file_list": []}',
    ),
)


@pytest.mark.parametrize(("tool_name", "arguments"), _FORCED_TOOL_ARGUMENTS)
async def test_strict_router_forces_every_canonical_tool(
    monkeypatch: pytest.MonkeyPatch, tool_name: str, arguments: str
) -> None:
    """Every dispatchable canonical tool can be the strict forced choice."""
    captured: dict[str, Any] = {}
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call(tool_name, arguments)]),
        captured,
    )

    result = await select_agent_tool(
        "route this", allowed_tools=[tool_name], forced_tool=tool_name
    )

    assert result is not None
    assert result.tool_name == tool_name
    assert result.arguments
    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": tool_name},
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
    patch_expert_router(monkeypatch, expert_router, completion)

    with pytest.raises(ToolSelectionError):
        await select_agent_tool(
            "route this",
            allowed_tools=["ChatAgent"],
        )


async def test_select_agent_tool_none_without_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A content-only completion yields no selection (chat fallback)."""
    patch_expert_router(
        monkeypatch, expert_router, _completion(content="just chatting")
    )
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
    patch_expert_router(
        monkeypatch, expert_router, SimpleNamespace(choices=[])
    )
    result = await select_agent_tool("hello", history=[])
    assert result is None


async def test_select_agent_tool_history_precedes_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History is sent before the current user turn for routing context."""
    captured: dict[str, Any] = {}
    patch_expert_router(
        monkeypatch,
        expert_router,
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
    patch_expert_router(
        monkeypatch,
        expert_router,
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


@pytest.fixture(autouse=True)
def _reset_tool_choice_cache() -> Any:
    """Isolate the process-level unsupported-endpoint set per test."""
    unsupported = getattr(expert_router, "_TOOL_CHOICE_REQUIRED_UNSUPPORTED")
    unsupported.clear()
    yield
    unsupported.clear()


def _bad_request(message: str) -> BadRequestError:
    """Build an offline OpenAI 400 with a controllable message body."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body=None)


_REQUIRED_REJECTION = (
    'tool_choice must either be a named tool or "auto". '
    'tool_choice="required" is not supported'
)


async def test_routing_falls_back_to_auto_on_required_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 on 'required' retries once with 'auto' and succeeds."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_REQUIRED_REJECTION)],
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
    )

    assert result == ToolSelection("ChatAgent", {})
    assert [call["tool_choice"] for call in calls] == ["required", "auto"]


async def test_routing_caches_unsupported_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After one 400 downgrade, later strict calls send 'auto' directly."""
    first_calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_REQUIRED_REJECTION)],
        calls=first_calls,
    )
    await select_agent_tool("first", allowed_tools=["ChatAgent"])
    assert [call["tool_choice"] for call in first_calls] == [
        "required",
        "auto",
    ]

    second_calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        calls=second_calls,
    )
    await select_agent_tool("second", allowed_tools=["ChatAgent"])

    # No wasted 400 round-trip: the single call goes straight to 'auto'.
    assert [call["tool_choice"] for call in second_calls] == ["auto"]


async def test_routing_unrelated_400_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 unrelated to tool_choice surfaces without an auto retry."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request("context length exceeded")],
        calls=calls,
    )

    with pytest.raises(expert_router.ExpertProviderError):
        await select_agent_tool("route this", allowed_tools=["ChatAgent"])

    assert [call["tool_choice"] for call in calls] == ["required"]


async def test_routing_forced_tool_400_is_not_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced named tool_choice that 400s is not downgraded to 'auto'."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_REQUIRED_REJECTION)],
        calls=calls,
    )

    with pytest.raises(expert_router.ExpertProviderError):
        await select_agent_tool(
            "route this",
            allowed_tools=["ChatAgent"],
            forced_tool="ChatAgent",
        )

    # Only the bare 'required' sentinel is eligible for downgrade.
    assert len(calls) == 1
    assert calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "ChatAgent"},
    }
