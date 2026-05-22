# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the mcp_client_phytomni LLM-assisted tool router.

Pins ``PhytomniToolRouter.from_env`` env-var handling and the
``route_query`` happy / no-tool / unsupported-tool branches by patching
the AsyncOpenAI client and the MCP client with stub implementations.
Also covers the small ``_tool_arguments`` / ``_tool_choice`` helpers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from mcp_client_phytomni.client import (
    PhytomniMcpClient,
    PhytomniToolRouter,
    RoutedQueryResult,
    ToolCallError,
    _tool_arguments,
    _tool_choice,
)
from mcp_client_phytomni.tool_result_formatters import FormattedToolResult

pytestmark = pytest.mark.unit


def _completion(
    *,
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """Build a fake OpenAI ChatCompletion shaped like AsyncOpenAI returns."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _openai_client(completion: SimpleNamespace) -> SimpleNamespace:
    """Wrap a completion into the chat.completions.create surface."""
    create = AsyncMock(return_value=completion)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        _create=create,  # exposed for assertion convenience
    )


def _mcp_client(
    *,
    tool_response: SimpleNamespace | None = None,
) -> SimpleNamespace:
    """Build a stub MCP client with openai_tools / call_tool coroutines."""
    return SimpleNamespace(
        openai_tools=AsyncMock(return_value=[{"type": "function"}]),
        call_tool=AsyncMock(return_value=tool_response),
    )


def test_tool_choice_returns_none_when_no_forced_tool() -> None:
    """``_tool_choice(None)`` lets the LLM auto-pick a tool."""
    assert _tool_choice(None) is None


def test_tool_choice_builds_openai_forced_function_dict() -> None:
    """A non-None tool name maps to OpenAI's function-tool descriptor."""
    assert _tool_choice("ChatAgent") == {
        "type": "function",
        "function": {"name": "ChatAgent"},
    }


def test_tool_arguments_parses_json_object() -> None:
    """``_tool_arguments`` decodes a JSON object payload."""
    assert _tool_arguments('{"query": "x"}') == {"query": "x"}


def test_tool_arguments_defaults_empty_string_to_empty_dict() -> None:
    """Empty arguments string is treated as ``{}`` rather than a TypeError."""
    assert _tool_arguments("") == {}


def test_tool_arguments_rejects_non_object_payload() -> None:
    """A JSON array fails loudly so callers cannot smuggle a list payload."""
    with pytest.raises(ValueError):
        _tool_arguments("[1, 2]")


def test_from_env_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_env`` aborts early when the API key env var is unset."""
    monkeypatch.delenv("OPENAI_API_KEY_CLIENT", raising=False)
    monkeypatch.setenv("MODEL_CLIENT", "gpt-test")

    with pytest.raises(RuntimeError) as excinfo:
        PhytomniToolRouter.from_env(cast(PhytomniMcpClient, SimpleNamespace()))

    assert "OPENAI_API_KEY_CLIENT" in str(excinfo.value)


def test_from_env_raises_when_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_env`` aborts early when the model env var is unset."""
    monkeypatch.setenv("OPENAI_API_KEY_CLIENT", "sk-test")
    monkeypatch.delenv("MODEL_CLIENT", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        PhytomniToolRouter.from_env(cast(PhytomniMcpClient, SimpleNamespace()))

    assert "MODEL_CLIENT" in str(excinfo.value)


def test_from_env_returns_router_with_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both vars are set the factory hands back a configured router."""
    monkeypatch.setenv("OPENAI_API_KEY_CLIENT", "sk-test")
    monkeypatch.setenv("MODEL_CLIENT", "gpt-router")
    monkeypatch.setenv("BASE_URL_CLIENT", "https://example.invalid")

    mcp_client_stub = cast(PhytomniMcpClient, SimpleNamespace())
    router = PhytomniToolRouter.from_env(mcp_client_stub)

    assert router.model == "gpt-router"
    assert router.mcp_client is mcp_client_stub


async def test_route_query_returns_plain_content_when_no_tool_call() -> None:
    """When OpenAI replies without a tool call, the answer is just text."""
    openai = _openai_client(_completion(content="No tool needed."))
    router = PhytomniToolRouter(
        cast(PhytomniMcpClient, _mcp_client()),
        openai_client=cast(AsyncOpenAI, openai),
        model="gpt-test",
    )

    result = await router.route_query("hello")

    assert isinstance(result, RoutedQueryResult)
    assert result.answer == "No tool needed."
    assert result.tool_response is None


async def test_route_query_calls_selected_mcp_tool() -> None:
    """A tool_call in the completion routes to mcp_client.call_tool."""
    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name="ChatAgent",
            arguments='{"user_query":"hi"}',
        )
    )
    openai = _openai_client(_completion(tool_calls=[tool_call]))
    mcp_response = SimpleNamespace(
        formatted=FormattedToolResult(
            answer="Tool answered.",
            follow_up_questions=("More?",),
        )
    )
    mcp_client = _mcp_client(tool_response=mcp_response)

    router = PhytomniToolRouter(
        cast(PhytomniMcpClient, mcp_client),
        openai_client=cast(AsyncOpenAI, openai),
        model="gpt-test",
    )

    result = await router.route_query("hi")

    assert result.answer == "Tool answered."
    assert result.follow_up_questions == ("More?",)
    mcp_client.call_tool.assert_awaited_once_with(
        "ChatAgent", {"user_query": "hi"}
    )


async def test_route_query_raises_on_custom_tool_call() -> None:
    """OpenAI custom tools (no ``function`` field) raise ToolCallError.

    The router only handles function-style tool calls because MCP tools
    map onto the function shape. Custom tools should fail loudly so a
    caller is not silently dropped without an answer.
    """
    tool_call = SimpleNamespace(function=None)
    openai = _openai_client(_completion(tool_calls=[tool_call]))
    router = PhytomniToolRouter(
        cast(PhytomniMcpClient, _mcp_client()),
        openai_client=cast(AsyncOpenAI, openai),
        model="gpt-test",
    )

    with pytest.raises(ToolCallError):
        await router.route_query("hi")


async def test_route_query_threads_history_into_openai_messages() -> None:
    """Prior chat history is forwarded as the OpenAI messages prefix."""
    openai = _openai_client(_completion(content="done"))
    router = PhytomniToolRouter(
        cast(PhytomniMcpClient, _mcp_client()),
        openai_client=cast(AsyncOpenAI, openai),
        model="gpt-test",
    )

    history: list[dict[str, Any]] = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier-answer"},
    ]

    await router.route_query("now", history=history)

    sent_messages = openai._create.await_args.kwargs["messages"]
    assert sent_messages == history + [{"role": "user", "content": "now"}]
