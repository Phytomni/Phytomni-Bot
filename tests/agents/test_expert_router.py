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

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, InternalServerError

from mcp_server_phytomni.agents.expert import (
    ExpertRoutingContractError,
    ExpertRoutingDeclinedError,
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


@pytest.mark.parametrize(
    "completion",
    [
        SimpleNamespace(choices=[]),
        _completion(content="just chatting"),
        _completion(tool_calls=[]),
    ],
    ids=("empty-choices", "content-only", "empty-tool-calls"),
)
async def test_select_expert_tool_decline_raises_declined(
    completion: object,
) -> None:
    """A strict decline raises the distinct ``ExpertRoutingDeclinedError``.

    An empty-choice or tool-call-free completion is the model answering
    directly rather than a contract fault, so the strict seam raises the
    narrower ``ExpertRoutingDeclinedError`` (a ``ToolSelectionError`` subclass
    the HTTP layer catches first to degrade to chat) rather than a bare
    ``ToolSelectionError``.
    """

    async def fake_completion(**_kwargs: Any) -> object:
        return completion

    with pytest.raises(ExpertRoutingDeclinedError):
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


async def test_select_expert_tool_genuine_violation_not_declined() -> None:
    """A real contract violation stays a plain ``ToolSelectionError``.

    An out-of-allowlist pick is a genuine fault, not a decline, so it must
    NOT raise ``ExpertRoutingDeclinedError`` -- otherwise the HTTP layer would
    wrongly degrade a misbehaving selector to chat.
    """

    async def fake_completion(**_kwargs: Any) -> object:
        return _completion(tool_calls=[_tool_call("DataAgent", "{}")])

    with pytest.raises(ToolSelectionError) as exc_info:
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
    assert not isinstance(exc_info.value, ExpertRoutingDeclinedError)


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


def _connection_error() -> APIConnectionError:
    """Build an offline OpenAI transport failure."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    return APIConnectionError(request=request)


def _server_error() -> InternalServerError:
    """Build an offline OpenAI 5xx response."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    response = httpx.Response(503, request=request)
    return InternalServerError(
        "upstream unavailable", response=response, body=None
    )


# The real Huawei pangu ``mastudio`` endpoint rejects a named tool_choice with
# a generic ``PANGU.3342`` 400 whose text does NOT mention "tool_choice", and
# rejects "required" with a validation 400 that does. Endpoint detection must
# not depend on the text: any 400 on a constrained choice triggers the auto
# fallback.
_PANGU_3342 = (
    "Error code: 400 - {'error': {'code': 'PANGU.3342', "
    "'type': 'BadRequestError', 'message': 'Failed to invoke the "
    "inference service. please check the details field.'}}"
)
_REQUIRED_REJECTION = (
    'tool_choice must either be a named tool or "auto". '
    'tool_choice="required" is not supported'
)


async def test_routing_falls_back_to_auto_on_required_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 on 'required' retries once with 'auto' over the full tools."""
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
    # The retry keeps the full allowlist; narrowing to one tool makes the
    # model return an empty tool call on the real endpoint.
    assert [t["function"]["name"] for t in calls[1]["tools"]] == [
        "KnowledgeAgent",
        "ChatAgent",
    ]


async def test_complete_expert_routing_retries_transient_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient provider timeouts get bounded retries with backoff."""
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[
            httpx.TimeoutException("transient timeout"),
            httpx.TimeoutException("transient timeout"),
        ],
        calls=calls,
    )

    result = await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "route this"}],
        tools=agent_openai_tool_specs(),
        tool_choice="auto",
    )

    assert result.choices
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert all(delay >= 0 for delay in sleeps)


@pytest.mark.parametrize(
    "transient_error",
    [_connection_error(), _server_error()],
    ids=("connection", "server-5xx"),
)
async def test_complete_expert_routing_retries_transient_sdk_errors(
    monkeypatch: pytest.MonkeyPatch, transient_error: BaseException
) -> None:
    """SDK connection and 5xx errors receive the same bounded retry policy."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[transient_error],
        calls=calls,
    )

    result = await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "route this"}],
        tools=agent_openai_tool_specs(),
        tool_choice="auto",
    )

    assert result.choices
    assert len(calls) == 2


async def test_complete_expert_routing_exhausts_transient_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry exhaustion stays a typed timeout and never loops forever."""
    calls: list[dict[str, Any]] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[
            httpx.TimeoutException("transient timeout"),
            httpx.TimeoutException("transient timeout"),
            httpx.TimeoutException("transient timeout"),
        ],
        calls=calls,
    )

    with pytest.raises(expert_router.ExpertProviderTimeoutError):
        await expert_router.complete_expert_routing(
            messages=[{"role": "user", "content": "route this"}],
            tools=agent_openai_tool_specs(),
            tool_choice="auto",
        )

    assert len(calls) == 3
    assert len(sleeps) == 2


async def test_routing_falls_back_on_pangu_3342_without_tool_choice_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constrained 400 whose text omits 'tool_choice' still falls back.

    The real ``PANGU.3342`` rejection does not mention ``tool_choice``; the
    fallback must key off the constrained choice, not the error text.
    """
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_PANGU_3342)],
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


async def test_routing_auto_400_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 on an unconstrained 'auto' call surfaces with no further retry.

    Only a constrained choice ('required' or a named tool) is eligible for
    the auto downgrade; a 400 already on 'auto' has nowhere to fall back to.
    """
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request("context length exceeded")],
        calls=calls,
    )

    with pytest.raises(expert_router.ExpertProviderError):
        # No allowlist -> unconstrained 'auto' request.
        await select_agent_tool("route this", history=[])

    assert [call["tool_choice"] for call in calls] == ["auto"]


async def test_routing_forced_tool_400_coerces_auto_pick_to_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced route that 400s downgrades to auto and coerces the pick.

    The real endpoint rejects a named ``tool_choice`` and, once retried with
    ``"auto"`` over the full allowlist, may autonomously pick a *different*
    tool. Because the caller pinned ``forced_tool``, the final selection is
    coerced back to it -- the user's explicit ``@agent`` wins.
    """
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        # On the auto retry the model autonomously picks ChatAgent.
        _completion(
            tool_calls=[_tool_call("ChatAgent", '{"user_query": "q"}')]
        ),
        side_effects=[_bad_request(_PANGU_3342)],
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["ChatAgent", "KnowledgeAgent", "DataAgent"],
        forced_tool="KnowledgeAgent",
    )

    # The user forced KnowledgeAgent, so that is the final selection even
    # though the model picked ChatAgent under the degraded auto retry.
    assert result is not None
    assert result.tool_name == "KnowledgeAgent"
    # First attempt: the named forced choice over the full allowlist.
    assert calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "KnowledgeAgent"},
    }
    # Retry: 'auto' over the FULL allowlist (narrowing makes the model
    # return an empty tool call on the real endpoint).
    assert calls[1]["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in calls[1]["tools"]] == [
        "ChatAgent",
        "KnowledgeAgent",
        "DataAgent",
    ]


async def test_routing_forced_tool_400_empty_pick_still_coerces_to_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced route survives an empty auto retry by minting the forced pick.

    On the real endpoint ``"auto"`` over a single narrowed tool can return no
    tool call at all; over the full allowlist the model may still decline. A
    forced route must not 502 in that case -- it mints the forced selection.
    """
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        # The auto retry returns a content-only completion (no tool call).
        _completion(content="no tool needed"),
        side_effects=[_bad_request(_PANGU_3342)],
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
        forced_tool="KnowledgeAgent",
    )

    assert result is not None
    assert result.tool_name == "KnowledgeAgent"
    assert result.arguments == {}
    # Forced first attempt is the named dict; then the auto retry.
    assert calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "KnowledgeAgent"},
    }
    assert calls[1]["tool_choice"] == "auto"
