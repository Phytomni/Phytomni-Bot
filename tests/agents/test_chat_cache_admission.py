# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Regression tests for Chat completion cache admission."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR
from openai import AsyncOpenAI as RealAsyncOpenAI

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.chat.completion_validation import (
    InvalidChatCompletionError,
    is_cacheable_chat_completion,
    require_successful_chat_completion,
)
from mcp_server_phytomni.func_cache import func_cache
from tests.support.outbound_fakes import patch_openai_runtime

pytestmark = pytest.mark.agent


def _chat_cache_call(query: str) -> chat_service.ChatCacheCall:
    """Build one stable Chat cache call for SDK boundary tests.

    Args:
        query: Unique user message used by the test.

    Returns:
        Complete keyword payload for ``run_phyto_chat_cached``.
    """
    return {
        "messages": [{"role": "user", "content": query}],
        "model": "pytest-model",
        "api_key": "test-key",
        "temperature": 0.3,
        "base_url": "https://provider.invalid/v1",
        "top_p": 1.0,
        "user": "test-user",
        "frequency_penalty": 0.0,
        "timeout": 3.0,
        "presence_penalty": 0.0,
        "stream": False,
        "n": 1,
        "max_tokens": None,
        "response_format": {"type": "json_object"},
        "reasoning_effort": None,
    }


def _phyto_chat_kwargs(max_retries: int) -> dict[str, Any]:
    """Build explicit public Chat options for SDK retry tests."""
    return {
        "api_key": "test-key",
        "base_url": "https://provider.invalid/v1",
        "model": "pytest-model",
        "max_retries": max_retries,
        "response_format": {"type": "json_object"},
        "stream": False,
        "timeout": 3.0,
    }


def _successful_completion(content: str) -> dict[str, Any]:
    """Build one minimal real-SDK ChatCompletion response payload."""
    return {
        "id": "chatcmpl-cache-admission",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "pytest-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _install_real_sdk_transport(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
) -> tuple[httpx.AsyncClient, dict[str, int]]:
    """Install a real OpenAI SDK client over scripted HTTP responses.

    Args:
        monkeypatch: Fixture replacing the service SDK constructor.
        responses: Ordered HTTP 200 response bodies.

    Returns:
        Underlying HTTP client and mutable request counter.
    """
    counter = {"requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the next scripted provider response."""
        index = counter["requests"]
        counter["requests"] += 1
        return httpx.Response(
            200,
            json=responses[index],
            request=request,
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    )
    sdk_client = RealAsyncOpenAI(
        api_key="test-key",
        base_url="https://provider.invalid/v1",
        http_client=http_client,
        max_retries=0,
    )
    # OpenAI 2.37 resolves platform headers through blocking host discovery on
    # the first async request. Pin only that header metadata so these transport
    # and response-parsing regressions stay deterministic in sandboxes.
    vars(sdk_client)["_platform"] = "Linux"
    patch_openai_runtime(monkeypatch, chat_service, sdk_client)
    return http_client, counter


def test_chat_completion_policy_rejects_top_level_error() -> None:
    """Reject a provider error object without retaining its message."""
    payload = {
        "error": {"type": "server_error", "message": "do-not-expose"},
        "choices": None,
    }

    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(InvalidChatCompletionError) as raised:
        require_successful_chat_completion(payload)
    assert raised.value.reason == "top_level_error"
    assert "do-not-expose" not in str(raised.value)


def test_chat_completion_policy_rejects_non_dictionary_payload() -> None:
    """Classify a non-dictionary value without retaining its contents."""
    payload = "invalid-payload-secret"

    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(InvalidChatCompletionError) as raised:
        require_successful_chat_completion(payload)
    assert raised.value.reason == "invalid_payload"
    assert payload not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("error_msg", "provider-failed"), ("error_code", 503)],
)
def test_chat_completion_policy_rejects_legacy_error_fields(
    field: str,
    value: object,
) -> None:
    """Reject non-empty legacy provider error markers.

    Args:
        field: Legacy top-level error field under test.
        value: Non-success marker supplied for the field.
    """
    payload = {
        field: value,
        "choices": [{"message": {"content": "unused"}}],
    }

    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(
        InvalidChatCompletionError,
        match="top_level_error",
    ):
        require_successful_chat_completion(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": None},
        {"choices": {}},
        {"choices": []},
        {"code": 500, "message": "provider failure"},
    ],
)
def test_chat_completion_policy_requires_non_empty_choices(
    payload: object,
) -> None:
    """Reject missing, null, non-list, and empty choices.

    Args:
        payload: Completion candidate with an invalid choices shape.
    """
    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(
        InvalidChatCompletionError,
        match="missing_choices",
    ):
        require_successful_chat_completion(payload)


def test_chat_completion_policy_requires_object_first_choice() -> None:
    """Reject a non-object first choice."""
    payload = {"choices": ["invalid"]}

    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(
        InvalidChatCompletionError,
        match="invalid_choice",
    ):
        require_successful_chat_completion(payload)


@pytest.mark.parametrize(
    "choice",
    [
        {},
        {"message": None},
        {"message": "invalid"},
        {"message": {"role": "assistant"}},
    ],
)
def test_chat_completion_policy_requires_canonical_message_output(
    choice: object,
) -> None:
    """Reject a missing, non-object, or output-free assistant message.

    Args:
        choice: First choice carrying an invalid message shape.
    """
    payload = {"choices": [choice]}

    assert not is_cacheable_chat_completion(payload)
    with pytest.raises(
        InvalidChatCompletionError,
        match="invalid_message",
    ):
        require_successful_chat_completion(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": {"content": "answer"}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"reasoning_content": "reason"}}]},
        {"choices": [{"message": {"tool_calls": []}}]},
        {"choices": [{"message": {"function_call": {}}}]},
        {"choices": [{"message": {"refusal": "cannot comply"}}]},
        {
            "choices": [
                {
                    "finish_reason": "content_filter",
                    "message": {"content": None},
                }
            ]
        },
        {
            "code": 200,
            "message": "provider metadata",
            "choices": [{"message": {"content": "answer"}}],
        },
        {"choices": [{"message": {"content": "upstream error"}}]},
    ],
)
def test_chat_completion_policy_accepts_compatible_outputs(
    payload: dict[str, object],
) -> None:
    """Accept canonical output shapes without inspecting their text.

    Args:
        payload: Structurally valid completion candidate.
    """
    assert is_cacheable_chat_completion(payload)
    assert require_successful_chat_completion(payload) is payload


@pytest.mark.parametrize("error_code", [None, "", 0, "0"])
def test_chat_completion_policy_accepts_success_error_code_sentinels(
    error_code: object,
) -> None:
    """Accept only the historical success sentinels for error_code.

    Args:
        error_code: Legacy success marker supplied by a provider.
    """
    payload = {
        "error_code": error_code,
        "error_msg": "",
        "choices": [{"message": {"content": "answer"}}],
    }

    assert is_cacheable_chat_completion(payload)


async def test_chat_cache_rejects_sdk_http_200_error_then_caches_recovery(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reject a real-SDK error value and cache only its recovery.

    Args:
        monkeypatch: Fixture replacing the service SDK constructor.
        caplog: Fixture capturing sanitized cache and service logs.
    """
    secret = "sdk-provider-secret"
    responses = [
        {"error": {"message": secret, "type": "server_error"}},
        _successful_completion("recovered"),
    ]
    http_client, counter = _install_real_sdk_transport(
        monkeypatch,
        responses,
    )
    chat_service.clear_chat_cache()
    call = _chat_cache_call("sdk-http-200-cache-admission")

    try:
        async with asyncio.timeout(5):
            with pytest.raises(InvalidChatCompletionError) as raised:
                await chat_service.run_phyto_chat_cached(**call)
            recovered = await chat_service.run_phyto_chat_cached(**call)
            cached = await chat_service.run_phyto_chat_cached(**call)
    finally:
        await asyncio.wait_for(http_client.aclose(), timeout=1)

    assert raised.value.reason == "top_level_error"
    assert recovered["choices"][0]["message"]["content"] == "recovered"
    assert cached == recovered
    assert counter["requests"] == 2
    assert secret not in str(raised.value)
    assert secret not in caplog.text


def test_chat_policy_lazily_rejects_historical_cached_error(
    tmp_path: Path,
) -> None:
    """Evict a historical error through the public cache seam.

    Args:
        tmp_path: Temporary directory for the isolated cache database.
    """
    policy = {"strict": False}
    calls = 0

    def admission(payload: Any) -> bool:
        """Enable the Chat structural policy after seeding history."""
        if not policy["strict"]:
            return True
        return is_cacheable_chat_completion(payload)

    @func_cache(
        db_path=str(tmp_path / "chat-legacy-admission.sqlite"),
        cache_if=admission,
    )
    def fetch() -> dict[str, Any]:
        """Return one historical error followed by a valid completion."""
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"error": {"message": "historical"}}
        return _successful_completion("recovered")

    assert "error" in fetch()
    policy["strict"] = True
    recovered = fetch()
    assert fetch() == recovered
    assert recovered["choices"][0]["message"]["content"] == "recovered"
    assert calls == 2


async def test_phyto_chat_retries_invalid_completion_then_caches_success(
    monkeypatch: pytest.MonkeyPatch,
    instant_retry_sleep: None,
) -> None:
    """Retry an invalid completion and cache the successful recovery.

    Args:
        monkeypatch: Fixture replacing the service SDK constructor.
        instant_retry_sleep: Fixture removing retry backoff delays.
    """
    del instant_retry_sleep
    responses = [
        {"error": {"message": "retry-provider-secret"}},
        _successful_completion("recovered-after-retry"),
    ]
    http_client, counter = _install_real_sdk_transport(
        monkeypatch,
        responses,
    )
    chat_service.clear_chat_cache()
    kwargs = _phyto_chat_kwargs(max_retries=1)

    try:
        async with asyncio.timeout(5):
            recovered = await chat_service.phyto_chat(
                "sdk-invalid-then-success",
                **kwargs,
            )
            cached = await chat_service.phyto_chat(
                "sdk-invalid-then-success",
                **kwargs,
            )
    finally:
        await asyncio.wait_for(http_client.aclose(), timeout=1)

    assert recovered["choices"][0]["message"]["content"] == (
        "recovered-after-retry"
    )
    assert cached == recovered
    assert counter["requests"] == 2


async def test_phyto_chat_invalid_completion_exhaustion_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    instant_retry_sleep: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bound persistent invalid completions behind a sanitized MCP error.

    Args:
        monkeypatch: Fixture replacing the service SDK constructor.
        instant_retry_sleep: Fixture removing retry backoff delays.
        caplog: Fixture capturing sanitized retry logs.
    """
    del instant_retry_sleep
    secret = "persistent-provider-secret"
    responses = [
        {"error": {"message": secret, "type": "server_error"}}
        for _ in range(3)
    ]
    http_client, counter = _install_real_sdk_transport(
        monkeypatch,
        responses,
    )
    chat_service.clear_chat_cache()

    try:
        async with asyncio.timeout(5):
            with pytest.raises(McpError) as raised:
                await chat_service.phyto_chat(
                    "sdk-persistent-invalid",
                    **_phyto_chat_kwargs(max_retries=2),
                )
    finally:
        await asyncio.wait_for(http_client.aclose(), timeout=1)

    assert counter["requests"] == 3
    assert raised.value.error.code == INTERNAL_ERROR
    assert str(raised.value) == "Failed to generate from Phyto"
    assert secret not in str(raised.value)
    assert raised.value.__cause__ is not None
    assert secret not in str(raised.value.__cause__)
    assert secret not in caplog.text
