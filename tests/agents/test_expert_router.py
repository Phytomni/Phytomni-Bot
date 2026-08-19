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
import logging
from types import SimpleNamespace
from typing import Any, cast

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
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.mcp.schemas import agent_openai_tool_specs
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from tests.support.expert_router_fakes import patch_expert_router
from tests.support.outbound_fakes import (
    assert_started_pool_attempts,
    recording_openai_resources,
    recording_outbound_runtime,
)

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
    assert captured["tool_choice"] == "auto"


async def test_strict_router_forces_requested_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced strict route skips the routing model and pins the tool."""
    captured: dict[str, Any] = {}
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("KnowledgeAgent", "{}")]),
        captured,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
        forced_tool="ChatAgent",
    )

    assert result == ToolSelection("ChatAgent", {"user_query": "route this"})
    assert not captured


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
    assert captured["tool_choice"] == "auto"
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
                allowed_tools=("ChatAgent", "KnowledgeAgent"),
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
    narrower ``ExpertRoutingDeclinedError`` (a ``ToolSelectionError``
    subclass). The HTTP layer may degrade that decline to ChatAgent when
    the caller allowed it; genuine violations stay a contract error.
    """

    async def fake_completion(**_kwargs: Any) -> object:
        return completion

    with pytest.raises(ExpertRoutingDeclinedError):
        await select_expert_tool(
            user_query="route this",
            history=[],
            options=ExpertRoutingOptions(
                allowed_tools=("ChatAgent", "KnowledgeAgent"),
                forced_tool=None,
                locale="en-US",
                completion=fake_completion,
            ),
        )


async def test_select_expert_tool_genuine_violation_not_declined() -> None:
    """A real contract violation stays a plain ``ToolSelectionError``.

    An out-of-allowlist pick is a genuine fault, not a decline, so it must
    NOT raise ``ExpertRoutingDeclinedError`` -- the HTTP layer must preserve
    the distinction while mapping both outcomes to a safe contract error.
    """

    async def fake_completion(**_kwargs: Any) -> object:
        return _completion(tool_calls=[_tool_call("DataAgent", "{}")])

    with pytest.raises(ToolSelectionError) as exc_info:
        await select_expert_tool(
            user_query="route this",
            history=[],
            options=ExpertRoutingOptions(
                allowed_tools=("ChatAgent", "KnowledgeAgent"),
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
        '{"species_code": "ath", "gene_id": "AT1G01010"}',
    ),
    (
        "GeneNetworkAgent",
        '{"species_code": "ath", "to_id": "TO:0000001"}',
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
    if tool_name in {
        "DeepGenomeAgent",
        "DigitalDesignAgent",
        "GeneNetworkAgent",
    }:
        assert result.arguments == {}
    else:
        assert result.arguments == {"user_query": "route this"}
    assert not captured


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
            allowed_tools=["ChatAgent", "KnowledgeAgent"],
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
    return BadRequestError(message, response=cast(Any, response), body=None)


def _connection_error() -> APIConnectionError:
    """Build an offline OpenAI transport failure."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    return APIConnectionError(request=cast(Any, request))


def _server_error() -> InternalServerError:
    """Build an offline OpenAI 5xx response."""
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    response = httpx.Response(503, request=request)
    return InternalServerError(
        "upstream unavailable", response=cast(Any, response), body=None
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


async def test_public_expert_completion_records_only_one_llm_operation() -> (
    None
):
    """The real Expert provider adapter changes only the shared LLM pool."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        """Record the outer provider call and return one tool selection."""
        calls.append(dict(kwargs))
        return _completion(
            tool_calls=[_tool_call("ChatAgent", '{"user_query":"hello"}')]
        )

    async with recording_outbound_runtime(
        config=ServerConfig(),
        resources=recording_openai_resources(create),
    ) as runtime:
        result = await expert_router.complete_expert_routing(
            messages=[{"role": "user", "content": "route this"}],
            tools=agent_openai_tool_specs(),
            tool_choice="auto",
        )

        assert result.choices
        assert len(calls) == 1
        assert calls[0]["tool_choice"] == "auto"
        assert_started_pool_attempts(runtime, {OutboundPoolName.LLM: 1})
        llm = runtime.pools.snapshot(OutboundPoolName.LLM)
        assert llm.completed == 1
        assert llm.in_use == llm.waiting == 0


async def test_unpinned_strict_routing_sends_auto_without_required_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first unpinned ≥2-tool call sends auto; it never probes required."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
    )

    assert result == ToolSelection("ChatAgent", {})
    assert [call["tool_choice"] for call in calls] == ["auto"]


async def test_routing_falls_back_to_auto_on_required_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 on 'required' retries once with 'auto' over the same tools."""
    calls: list[dict[str, Any]] = []
    tools = [
        spec
        for spec in agent_openai_tool_specs()
        if spec["function"]["name"] in {"KnowledgeAgent", "ChatAgent"}
    ]
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_REQUIRED_REJECTION)],
        calls=calls,
    )

    result = await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "route this"}],
        tools=tools,
        tool_choice="required",
    )

    assert result.choices
    assert [call["tool_choice"] for call in calls] == ["required", "auto"]
    # The retry keeps the offered tools; narrowing to one tool makes the
    # model return an empty tool call on the real endpoint.
    assert [t["function"]["name"] for t in calls[1]["tools"]] == [
        tool["function"]["name"] for tool in tools
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

    result = await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "route this"}],
        tools=agent_openai_tool_specs(),
        tool_choice="required",
    )

    assert result.choices
    assert [call["tool_choice"] for call in calls] == ["required", "auto"]


async def test_routing_caches_unsupported_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After one 400 downgrade, later constrained calls send 'auto'."""
    first_calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request(_REQUIRED_REJECTION)],
        calls=first_calls,
    )
    await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "first"}],
        tools=agent_openai_tool_specs(),
        tool_choice="required",
    )
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
    await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": "second"}],
        tools=agent_openai_tool_specs(),
        tool_choice="required",
    )

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


async def test_unpinned_strict_auto_400_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 on the unpinned auto call is a provider error, not a probe."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[_bad_request("context length exceeded")],
        calls=calls,
    )

    with pytest.raises(expert_router.ExpertProviderError):
        await select_agent_tool(
            "route this",
            allowed_tools=["KnowledgeAgent", "ChatAgent"],
        )

    assert [call["tool_choice"] for call in calls] == ["auto"]


async def test_routing_forced_tool_skips_the_routing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned tool never calls the routing model, even with two peers."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(
            tool_calls=[_tool_call("ChatAgent", '{"user_query": "q"}')]
        ),
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["ChatAgent", "KnowledgeAgent", "DataAgent"],
        forced_tool="KnowledgeAgent",
    )

    assert result == ToolSelection(
        "KnowledgeAgent", {"user_query": "route this"}
    )
    assert not calls


async def test_routing_singleton_allowlist_skips_the_routing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One authorized tool is already decided, so the model is not called."""
    calls: list[dict[str, Any]] = []
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(content="no tool needed"),
        calls=calls,
    )

    result = await select_agent_tool(
        "route this",
        allowed_tools=["KnowledgeAgent"],
    )

    assert result == ToolSelection(
        "KnowledgeAgent", {"user_query": "route this"}
    )
    assert not calls


_PROVIDER_LOGGER = "mcp_server_phytomni.agents.expert.routing_observability"
_PROVIDER_SENTINEL = "PROVIDER-PAYLOAD-SENTINEL transient timeout"


def _provider_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return Pangu attempt records emitted by complete_expert_routing."""
    return [
        record
        for record in caplog.records
        if record.name == _PROVIDER_LOGGER
        and record.getMessage().startswith("Expert routing provider completed")
    ]


async def test_complete_expert_routing_logs_ok_duration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful Pangu hop logs result=ok and a non-negative duration."""
    caplog.set_level(logging.INFO, logger=_PROVIDER_LOGGER)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
    )

    result = await expert_router.complete_expert_routing(
        messages=[{"role": "user", "content": _PROVIDER_SENTINEL}],
        tools=agent_openai_tool_specs(),
        tool_choice="auto",
    )

    assert result.choices
    records = _provider_records(caplog)
    assert len(records) == 1
    assert getattr(records[0], "result") == "ok"
    assert getattr(records[0], "duration_ms") >= 0
    assert getattr(records[0], "attempt") == 0
    assert "duration_ms=" in records[0].getMessage()
    assert _PROVIDER_SENTINEL not in records[0].getMessage()
    assert _PROVIDER_SENTINEL not in caplog.text


async def test_complete_expert_routing_logs_timeout_without_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retry exhaustion logs result=timeout and never the exception text."""

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    caplog.set_level(logging.INFO, logger=_PROVIDER_LOGGER)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _completion(tool_calls=[_tool_call("ChatAgent", "{}")]),
        side_effects=[
            httpx.TimeoutException(_PROVIDER_SENTINEL),
            httpx.TimeoutException(_PROVIDER_SENTINEL),
            httpx.TimeoutException(_PROVIDER_SENTINEL),
        ],
    )

    with pytest.raises(expert_router.ExpertProviderTimeoutError):
        await expert_router.complete_expert_routing(
            messages=[{"role": "user", "content": _PROVIDER_SENTINEL}],
            tools=agent_openai_tool_specs(),
            tool_choice="auto",
        )

    records = _provider_records(caplog)
    assert [getattr(record, "result") for record in records] == [
        "transient_retry",
        "transient_retry",
        "timeout",
    ]
    assert all(getattr(record, "duration_ms") >= 0 for record in records)
    assert _PROVIDER_SENTINEL not in caplog.text
