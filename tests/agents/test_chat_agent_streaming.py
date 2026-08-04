# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline tests for ``stream_phyto_chat_chunks`` streaming primitive.

Pins three behaviors: streaming yields provider chunks unchanged
(unknown fields survive), prepends OBS upload context when
``obs_file_list`` is non-empty, and retries open-stream once on
transient transport errors. Any mid-stream failure propagates
immediately since silent retry would lose delivered chunks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ConnectError, Request, TimeoutException
from mcp.shared.exceptions import McpError
from openai import APIConnectionError

from mcp_server_phytomni.agents.chat import service as chat_service

pytestmark = pytest.mark.agent


def _fake_chunk(payload: dict[str, Any]) -> SimpleNamespace:
    """Wrap a chunk payload so ``chunk.model_dump()`` returns it unchanged.

    Built as a ``SimpleNamespace`` to dodge pylint R0903 on a
    single-method stand-in; the foreign refactor at ``6a21961`` made
    this the codebase norm.
    """
    return SimpleNamespace(model_dump=lambda payload=payload: payload)


async def _iter_chunks(
    payloads: list[dict[str, Any]],
) -> AsyncIterator[SimpleNamespace]:
    """Yield each payload wrapped via :func:`_fake_chunk`.

    Args:
        payloads: Provider chunk payloads to emit in order.

    Yields:
        One ``SimpleNamespace`` per payload.
    """
    for payload in payloads:
        yield _fake_chunk(payload)


def _install_default_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``get_prompt`` so the primitive does not load real YAML."""
    monkeypatch.setattr(
        chat_service,
        "get_prompt",
        lambda *_a, **_k: "system prompt",
    )


def _build_fake_async_openai(
    create_fn: Any,
    captured: dict[str, Any],
) -> Any:
    """Build an ``AsyncOpenAI`` shim routing ``create`` to ``create_fn``.

    Returns a plain factory function (not a class) so the test fake
    stays a single-purpose stand-in without triggering pylint R0903
    on a single-method test class; foreign ``6a21961`` made
    ``SimpleNamespace``-based fakes the codebase norm for the same
    reason.

    Args:
        create_fn: Async callable that backs
            ``client.chat.completions.create``.
        captured: Dict the factory fills with ``api_key`` and
            ``base_url`` at construction time so tests can assert
            client wiring.

    Returns:
        A callable compatible with
        ``AsyncOpenAI(api_key=..., base_url=...)``.
    """

    def _factory(api_key: str, base_url: str) -> SimpleNamespace:
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn))
        )

    return _factory


def _stream_kwargs() -> dict[str, Any]:
    """Return a minimal complete kwargs set for the streaming primitive."""
    return {
        "prompt_file": "prompts.yaml",
        "prompt_path": "system/chat",
        "api_key": "api-key",
        "base_url": "https://example.invalid/v1",
        "model": "pytest-model",
        "frequency_penalty": 0.0,
        "n": 1,
        "presence_penalty": 0.0,
        "reasoning_effort": None,
        "response_format": {"type": "text"},
        "temperature": 0.1,
        "top_p": 1.0,
        "user": "u1",
        "timeout": 1.0,
        "max_tokens": 64,
        "max_retries": 0,
    }


async def test_stream_phyto_chat_chunks_yields_provider_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each provider chunk reaches the caller via ``model_dump`` unchanged.

    Pins the SSE contract: unknown provider fields (here ``custom``)
    survive untouched so the downstream OpenAI shaper can forward
    them verbatim in ``data: {...}\\n\\n`` frames.
    """
    _install_default_prompt(monkeypatch)
    captured: dict[str, Any] = {}
    payloads = [
        {"id": "c1", "choices": [{"delta": {"content": "Hel"}}]},
        {
            "id": "c1",
            "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}],
            "custom": "kept",
        },
    ]

    async def fake_create(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        """Capture request kwargs and return the streaming iterator."""
        captured["params"] = kwargs
        return _iter_chunks(payloads)

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, captured),
    )

    received: list[dict[str, Any]] = []
    async for chunk in chat_service.stream_phyto_chat_chunks(
        user_query="hi", locale="zh-CN", **_stream_kwargs()
    ):
        received.append(chunk)

    assert received == payloads
    assert captured["params"]["stream"] is True
    assert captured["params"]["model"] == "pytest-model"
    assert captured["api_key"] == "api-key"
    assert "Simplified Chinese" in captured["params"]["messages"][0]["content"]


async def test_stream_relay_carries_internal_timeout_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming relay calls identify their Agent budget to the relay."""
    _install_default_prompt(monkeypatch)
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-key")
    chat_service.get_sensitive_config.cache_clear()
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        captured["params"] = kwargs
        return _iter_chunks([])

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, captured),
    )
    kwargs = _stream_kwargs()
    kwargs["relay_timeout_profile"] = "phyto-knowledge"
    try:
        async for _ in chat_service.stream_phyto_chat_chunks(
            user_query="hi", **kwargs
        ):
            pass
    finally:
        chat_service.get_sensitive_config.cache_clear()

    assert captured["base_url"] == "https://relay.test/v1/relay/llm"
    assert captured["params"]["extra_headers"] == {
        "X-Phytomni-Relay-Timeout-Profile": "phyto-knowledge"
    }


async def test_stream_phyto_chat_chunks_prepends_upload_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty ``obs_file_list`` routes through ``download_list_convert``.

    Pins parity with :func:`phyto_chat`: the user query handed to the
    LLM carries the converted upload context, not the raw query, so
    streaming and non-streaming chat see identical context.
    """
    _install_default_prompt(monkeypatch)
    captured: dict[str, Any] = {}

    async def fake_download_list_convert(**kwargs: Any) -> list[str]:
        """Capture download kwargs and return converted upload text."""
        captured["download"] = kwargs
        return ["converted paper text"]

    def fake_format_upload_context(
        upload_str_list: list[str], _max_tokens: int
    ) -> tuple[str, int]:
        """Skip ``format_upload_context``'s token budget truncation.

        The real helper has its own tests; this test pins only that
        the streaming primitive plumbs the converted upload text
        through to the user message verbatim.
        """
        return "\n".join(upload_str_list), 0

    async def fake_create(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        """Capture request kwargs and return an empty streaming iterator."""
        captured["params"] = kwargs
        return _iter_chunks([])

    monkeypatch.setattr(
        chat_service,
        "download_list_convert",
        fake_download_list_convert,
    )
    monkeypatch.setattr(
        chat_service,
        "format_upload_context",
        fake_format_upload_context,
    )
    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, captured),
    )

    async for _ in chat_service.stream_phyto_chat_chunks(
        user_query="Summarize the paper.",
        obs_file_list=["obs://paper.pdf"],
        **_stream_kwargs(),
    ):
        pass

    user_message = captured["params"]["messages"][1]["content"]
    assert "converted paper text" in user_message
    assert "Summarize the paper." in user_message
    assert captured["download"]["obs_file_list"] == ["obs://paper.pdf"]


@pytest.mark.parametrize(
    "transient",
    [
        ConnectError("transient"),
        APIConnectionError(
            request=Request("POST", "https://example.invalid/v1")
        ),
    ],
)
async def test_stream_phyto_chat_chunks_open_retries_transient_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    transient: Exception,
) -> None:
    """A raw or SDK-wrapped open-stream transport error is retried.

    Policy (B): the first ``create`` raises a transient error, the
    second succeeds; the caller sees the stream without ever knowing
    about the retry. The transient class mirrors what
    :func:`_run_phyto_chat` retries, so non-stream and stream paths
    treat the same network blips identically.
    """
    _install_default_prompt(monkeypatch)
    # ``asyncio.sleep`` between retries is dead weight in a test.
    monkeypatch.setattr(chat_service.asyncio, "sleep", _noop_sleep)
    calls = {"count": 0}

    async def fake_create(**_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        """Raise once then succeed on the second attempt."""
        calls["count"] += 1
        if calls["count"] == 1:
            raise transient
        return _iter_chunks(
            [{"id": "c1", "choices": [{"delta": {"content": "ok"}}]}]
        )

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, {}),
    )

    received: list[dict[str, Any]] = []
    async for chunk in chat_service.stream_phyto_chat_chunks(
        user_query="hi", **_stream_kwargs()
    ):
        received.append(chunk)

    assert calls["count"] == 2
    assert received == [
        {"id": "c1", "choices": [{"delta": {"content": "ok"}}]}
    ]


async def test_stream_phyto_chat_chunks_open_retries_exhaust_raise_mcperror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive transient failures raise a sanitized ``McpError``.

    Pins the retry budget: ``MAX_OPEN_STREAM_RETRIES`` total attempts
    (the constant equals 1, so 1 retry + 1 original = 2 attempts);
    after that the primitive raises ``McpError`` carrying a generic
    message so backend transport details do not leak to clients.
    """
    _install_default_prompt(monkeypatch)
    monkeypatch.setattr(chat_service.asyncio, "sleep", _noop_sleep)
    calls = {"count": 0}

    async def fake_create(**_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        """Always raise to drive the open-stream retry to exhaustion."""
        calls["count"] += 1
        raise TimeoutException("repeat")

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, {}),
    )

    with pytest.raises(McpError) as excinfo:
        async for _ in chat_service.stream_phyto_chat_chunks(
            user_query="hi", **_stream_kwargs()
        ):
            pass

    assert calls["count"] == chat_service.MAX_OPEN_STREAM_RETRIES + 1
    message = excinfo.value.error.message
    assert "Failed to open chat completion stream" in message


async def test_stream_phyto_chat_chunks_mid_stream_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after the first chunk propagates without retry.

    Pins policy (B): once chunks are flowing the client has already
    received bytes; retrying would silently re-emit content or skip
    state, corrupting the SSE timeline. The primitive forwards the
    transient exception so the route can close the SSE response and
    let the client decide whether to resume.
    """
    _install_default_prompt(monkeypatch)
    monkeypatch.setattr(chat_service.asyncio, "sleep", _noop_sleep)
    calls = {"count": 0}

    async def fake_create(**_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        """Return an iterator that yields one chunk then raises."""
        calls["count"] += 1

        async def _iter() -> AsyncIterator[SimpleNamespace]:
            yield _fake_chunk(
                {"id": "c1", "choices": [{"delta": {"content": "Hi"}}]}
            )
            raise TimeoutException("mid-stream")

        return _iter()

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        _build_fake_async_openai(fake_create, {}),
    )

    received: list[dict[str, Any]] = []
    with pytest.raises(TimeoutException):
        async for chunk in chat_service.stream_phyto_chat_chunks(
            user_query="hi", **_stream_kwargs()
        ):
            received.append(chunk)

    # One open call, no retry: mid-stream is the client's resume
    # decision, not the primitive's silent retry.
    assert calls["count"] == 1
    assert len(received) == 1


async def _noop_sleep(_seconds: float) -> None:
    """Replace ``asyncio.sleep`` so retry tests stay instant.

    Args:
        _seconds: Ignored sleep duration.
    """
    return None
