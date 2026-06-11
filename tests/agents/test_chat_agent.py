# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for ChatAgent service helpers.

Covers upload conversion, prompt loading, OpenAI request construction, and
follow-up question attachment without external network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.chat import service as chat_agents

pytestmark = pytest.mark.agent


class FakeChatCompletion:
    """Small OpenAI response stand-in with the model_dump contract.

    Attributes:
        content: Assistant message content returned by the fake response.
    """

    def __init__(self, content: str = "ok"):
        """Verify init  ."""
        self.content = content

    def model_dump(self) -> dict[str, Any]:
        """Return a Chat Completions-style payload.

        Returns:
            Minimal response dictionary with assistant content.
        """
        return {
            "choices": [
                {
                    "message": {
                        "content": self.content,
                        "role": "assistant",
                    }
                }
            ]
        }

    def message_content(self) -> str:
        """Return the fake assistant message content.

        Returns:
            Stored assistant message content.
        """
        return self.content


async def test_phyto_chat_converts_uploads_and_builds_openai_request(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify phyto_chat converts uploads and builds OpenAI request.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace I/O clients.

    Returns:
        None after request payload assertions pass.
    """
    chat_agents.run_phyto_chat_cached.cache_clear()
    captured: dict[str, Any] = {}

    async def fake_download_list_convert(**kwargs: Any) -> list[str]:
        """Capture upload conversion kwargs and return converted text.

        Args:
            **kwargs: Upload conversion keyword arguments.

        Returns:
            Converted file text fragments.
        """
        captured["download"] = kwargs
        return ["converted paper text"]

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Capture prompt lookup arguments and return a system prompt.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path.
            params: Optional prompt rendering parameters.

        Returns:
            Static system prompt text.
        """
        captured["prompt"] = {
            "prompt_file": prompt_file,
            "prompt_path": prompt_path,
            "params": params,
        }
        return "system prompt"

    class FakeCompletions:
        """Capture OpenAI completion kwargs."""

        async def create(self, **kwargs: Any) -> FakeChatCompletion:
            """Capture completion kwargs and return a fake completion.

            Args:
                **kwargs: Chat completion request keyword arguments.

            Returns:
                Fake chat completion response.
            """
            captured["completion"] = kwargs
            return FakeChatCompletion("chat answer")

        def last_payload(self) -> dict[str, Any]:
            """Return the latest captured completion payload.

            Returns:
                Captured completion request payload.
            """
            return captured.get("completion", {})

    class FakeAsyncOpenAI:
        """Minimal AsyncOpenAI-compatible client.

        Attributes:
            chat: Fake chat namespace with a completions client.
        """

        def __init__(self, api_key: str, base_url: str):
            """Verify init  ."""
            captured["client"] = {
                "api_key": api_key,
                "base_url": base_url,
            }
            self.chat = SimpleNamespace(completions=FakeCompletions())

        def client_settings(self) -> dict[str, str]:
            """Return captured client connection settings.

            Returns:
                Captured api_key and base_url values.
            """
            return captured["client"]

        def completion_client(self) -> Any:
            """Return the fake completions client.

            Returns:
                Fake completions client instance.
            """
            return self.chat.completions

    monkeypatch.setattr(
        chat_agents, "download_list_convert", fake_download_list_convert
    )
    monkeypatch.setattr(chat_agents, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chat_agents, "AsyncOpenAI", FakeAsyncOpenAI)

    result = await chat_agents.phyto_chat(
        user_query="Summarize the paper.",
        obs_file_list=["obs://paper.pdf"],
        prompt_file="prompts.yaml",
        prompt_path="system/chat",
        api_key="api-key",
        base_url="https://example.invalid/v1",
        model="pytest-model",
        response_format={"type": "text"},
        timeout=3.0,
        max_retries=0,
        max_tokens=200,
    )

    assert result is not None
    assert result["choices"][0]["message"]["content"] == "chat answer"
    assert captured["client"] == {
        "api_key": "api-key",
        "base_url": "https://example.invalid/v1",
    }
    assert captured["download"]["obs_file_list"] == ["obs://paper.pdf"]
    assert captured["prompt"] == {
        "prompt_file": "prompts.yaml",
        "prompt_path": "system/chat",
        "params": None,
    }
    messages = captured["completion"]["messages"]
    assert messages[0] == {"role": "system", "content": "system prompt"}
    assert "converted paper text" in messages[1]["content"]
    assert "Summarize the paper." in messages[1]["content"]
    assert captured["completion"]["timeout"] == 3.0
    assert captured["completion"]["max_tokens"] == 200
    assert "reasoning_effort" not in captured["completion"]


async def test_phyto_chat_with_follow_attaches_follow_up_questions(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify phyto_chat_with_follow attaches follow-up questions.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace chat helpers.

    Returns:
        None after follow-up payload assertions pass.
    """
    calls: list[dict[str, Any]] = []

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        """Return main answer on first call and follow-ups on second.

        Args:
            **kwargs: Chat helper keyword arguments.

        Returns:
            Fake chat completion payload.
        """
        calls.append(kwargs)
        content = (
            "main answer"
            if len(calls) == 1
            else 'prefix ["What gene next?", "Which tissue?"] suffix'
        )
        return FakeChatCompletion(content).model_dump()

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Validate follow-up prompt rendering arguments.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path.
            params: Prompt rendering parameters.

        Returns:
            Static follow-up prompt text.
        """
        assert prompt_file == "prompts.yaml"
        assert prompt_path == "system/follow_up_questions"
        assert params == {
            "user_query": "Explain drought tolerance.",
            "system_response": "main answer",
        }
        return "follow-up prompt"

    monkeypatch.setattr(chat_agents, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(chat_agents, "get_prompt", fake_get_prompt)

    result = await chat_agents.phyto_chat_with_follow(
        user_query="Explain drought tolerance.",
        obs_file_list=["obs://context.pdf"],
        prompt_file="prompts.yaml",
        prompt_path="system/chat",
        api_key="api-key",
        base_url="https://example.invalid/v1",
        model="pytest-model",
        max_retries=0,
    )

    assert result is not None
    message = result["choices"][0]["message"]
    assert message["content"] == "main answer"
    assert message["follow_up_questions"] == [
        "What gene next?",
        "Which tissue?",
    ]
    assert calls[0]["obs_file_list"] == ["obs://context.pdf"]
    assert calls[1]["user_query"] == "follow-up prompt"


async def test_run_phyto_chat_cached_dedupes_identical_sampling(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the cache keys only on messages and response_format.

    Rotating infra params or sampling knobs (model, temperature, and the
    rest) reuses the first cached payload; only changing messages or
    response_format triggers a fresh completion.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace AsyncOpenAI.

    Returns:
        None after cache hit/miss assertions pass.
    """
    chat_agents.run_phyto_chat_cached.cache_clear()
    calls = {"create": 0}

    async def fake_create(**kwargs: Any) -> FakeChatCompletion:
        """Tally each completion request and return a fake response."""
        del kwargs
        calls["create"] += 1
        return FakeChatCompletion(f"answer-{calls['create']}")

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        """Return a SimpleNamespace mimicking AsyncOpenAI's surface."""
        del api_key, base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create),
            ),
        )

    monkeypatch.setattr(chat_agents, "AsyncOpenAI", fake_async_openai)

    sampling_kwargs: dict[str, Any] = {
        "messages": [{"role": "user", "content": "leaf growth"}],
        "model": "pytest-model",
        "temperature": 0.3,
        "top_p": 1.0,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "n": 1,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "reasoning_effort": None,
        "api_key": "ignored-1",
        "base_url": "https://example.invalid/v1",
        "user": "u",
        "timeout": 3.0,
        "stream": False,
    }

    first = await chat_agents.run_phyto_chat_cached(**sampling_kwargs)
    # Rotate every infra-only parameter on the second call to prove the
    # cache ignores them; the result must come from the first call's
    # cached payload, not a fresh completion.
    second = await chat_agents.run_phyto_chat_cached(
        **{
            **sampling_kwargs,
            "api_key": "ignored-2",
            "base_url": "https://other.invalid/v1",
            "user": "v",
            "timeout": 9.9,
            "stream": True,
        }
    )

    assert first == second
    assert calls["create"] == 1

    # temperature, model, and the other sampling knobs are no longer in
    # the cache key, so flipping temperature hits the first call's cached
    # payload instead of triggering a fresh completion.
    third = await chat_agents.run_phyto_chat_cached(
        **{**sampling_kwargs, "temperature": 0.9}
    )

    assert third == first
    assert calls["create"] == 1

    # messages and response_format are the only key fields, so changing
    # the prompt misses the cache and triggers a fresh completion.
    fourth = await chat_agents.run_phyto_chat_cached(
        **{
            **sampling_kwargs,
            "messages": [{"role": "user", "content": "root growth"}],
        }
    )

    assert fourth != first
    assert calls["create"] == 2


async def test_run_phyto_chat_cached_does_not_cache_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify a raising completion never stores a cache entry.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace AsyncOpenAI.

    Returns:
        None after failure/recovery assertions pass.
    """
    chat_agents.run_phyto_chat_cached.cache_clear()
    calls = {"create": 0}

    async def flakey_create(**kwargs: Any) -> FakeChatCompletion:
        """Raise on the first invocation, succeed afterwards."""
        del kwargs
        calls["create"] += 1
        if calls["create"] == 1:
            raise RuntimeError("first attempt fails")
        return FakeChatCompletion("recovered")

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        """Return a SimpleNamespace mimicking AsyncOpenAI's surface."""
        del api_key, base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=flakey_create),
            ),
        )

    monkeypatch.setattr(chat_agents, "AsyncOpenAI", fake_async_openai)

    sampling_kwargs: dict[str, Any] = {
        "messages": [{"role": "user", "content": "leaf growth"}],
        "model": "pytest-model",
        "temperature": 0.3,
        "top_p": 1.0,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "n": 1,
        "max_tokens": None,
        "response_format": {"type": "json_object"},
        "reasoning_effort": None,
        "api_key": "k",
        "base_url": "https://example.invalid/v1",
        "user": "u",
        "timeout": 3.0,
        "stream": False,
    }

    with pytest.raises(RuntimeError, match="first attempt fails"):
        await chat_agents.run_phyto_chat_cached(**sampling_kwargs)

    # The first failure did not poison the cache, so the next call
    # runs the underlying create() again and observes the recovered
    # payload.
    result = await chat_agents.run_phyto_chat_cached(**sampling_kwargs)

    assert result["choices"][0]["message"]["content"] == "recovered"
    assert calls["create"] == 2


async def test_non_streaming_repairs_reasoning_content_answer_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stream completions repair answers misplaced in reasoning_content."""
    chat_agents.run_phyto_chat_cached.cache_clear()

    class MisplacedReasoningCompletion:
        """Response stand-in with the answer tail in reasoning_content."""

        def model_dump(self) -> dict[str, Any]:
            """Return a provider-shaped payload with blank content."""
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": (
                                "<think>identify chlorophyll</think>"
                                "Leaves capture light."
                            ),
                        }
                    }
                ]
            }

    async def fake_create(**kwargs: Any) -> MisplacedReasoningCompletion:
        """Return the misplaced provider response."""
        assert kwargs["stream"] is False
        return MisplacedReasoningCompletion()

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        """Return a fake AsyncOpenAI client."""
        del api_key, base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create),
            ),
        )

    monkeypatch.setattr(chat_agents, "AsyncOpenAI", fake_async_openai)

    result = await chat_agents.run_phyto_chat_cached(
        messages=[{"role": "user", "content": "leaf color"}],
        model="pytest-misplaced-nonstream",
        temperature=0.3,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        n=1,
        max_tokens=None,
        response_format={"type": "text"},
        reasoning_effort=None,
        api_key="k",
        base_url="https://example.invalid/v1",
        user="u",
        timeout=3.0,
        stream=False,
    )

    message = result["choices"][0]["message"]
    assert message["content"] == "Leaves capture light."
    assert message["reasoning_content"] == "identify chlorophyll"


async def test_stream_response_to_dict_accumulates_reasoning_deltas() -> None:
    """Streaming aggregation merges delta.reasoning_content beside content.

    Reasoner-style providers (DeepSeek-R1, Qwen-Reasoner, ...) emit
    ``delta.reasoning_content`` chunks separately from
    ``delta.content``. The aggregator must accumulate both, propagate
    the last non-empty ``finish_reason``, and only attach
    ``reasoning_content`` to the rebuilt message when at least one
    reasoning chunk arrived so non-reasoner providers stay compact.
    """

    def _make_chunk(
        content: str = "",
        reasoning: str = "",
        finish_reason: str | None = None,
        seed: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        """Build one streaming chunk stand-in with the model_dump contract.

        The final chunk's ``seed`` becomes the rebuilt response base so
        provider metadata (id / usage / system_fingerprint) survives
        the aggregation; interim chunks contribute only deltas.
        """
        delta = SimpleNamespace(content=content, reasoning_content=reasoning)
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(
            choices=[choice],
            model_dump=lambda payload=seed or {}: payload,
        )

    chunks = [
        _make_chunk(content="The leaf ", reasoning="Identifying species. "),
        _make_chunk(
            content="is rice.",
            reasoning="Locus prefix Os01.",
            finish_reason="stop",
            seed={
                "id": "chatcmpl-stream",
                "usage": {"prompt_tokens": 9, "completion_tokens": 6},
            },
        ),
    ]

    async def fake_stream() -> Any:
        """Async generator that yields the prepared chunks in order."""
        for chunk in chunks:
            yield chunk

    stream_to_dict = getattr(chat_agents, "_stream_response_to_dict")
    result = await stream_to_dict(fake_stream())

    choice = result["choices"][0]
    assert choice["message"]["content"] == "The leaf is rice."
    assert choice["message"]["reasoning_content"] == (
        "Identifying species. Locus prefix Os01."
    )
    assert choice["finish_reason"] == "stop"
    assert result["id"] == "chatcmpl-stream"
    assert result["usage"]["prompt_tokens"] == 9


async def test_streaming_repairs_reasoning_content_after_aggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streamed reasoning tail is repaired after chunk aggregation."""
    chat_agents.run_phyto_chat_cached.cache_clear()

    def _make_chunk(
        reasoning: str = "",
        finish_reason: str | None = None,
        seed: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        """Build one stream chunk with only reasoning deltas."""
        delta = SimpleNamespace(content="", reasoning_content=reasoning)
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(
            choices=[choice],
            model_dump=lambda payload=seed or {}: payload,
        )

    async def fake_stream() -> Any:
        """Yield chunks whose aggregate reasoning carries the answer tail."""
        chunks = [
            _make_chunk("<think>identify chlorophyll"),
            _make_chunk("</think>Leaves capture light.", "stop"),
        ]
        for chunk in chunks:
            yield chunk

    async def fake_create(**kwargs: Any) -> Any:
        """Return the fake async stream."""
        assert kwargs["stream"] is True
        return fake_stream()

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        """Return a fake AsyncOpenAI client."""
        del api_key, base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create),
            ),
        )

    monkeypatch.setattr(chat_agents, "AsyncOpenAI", fake_async_openai)

    result = await chat_agents.run_phyto_chat_cached(
        messages=[{"role": "user", "content": "leaf color"}],
        model="pytest-misplaced-stream",
        temperature=0.3,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        n=1,
        max_tokens=None,
        response_format={"type": "text"},
        reasoning_effort=None,
        api_key="k",
        base_url="https://example.invalid/v1",
        user="u",
        timeout=3.0,
        stream=True,
    )

    choice = result["choices"][0]
    assert choice["message"]["content"] == "Leaves capture light."
    assert choice["message"]["reasoning_content"] == "identify chlorophyll"
    assert choice["finish_reason"] == "stop"


async def test_stream_response_omits_reasoning_for_plain_providers() -> None:
    """Non-reasoner streams keep the rebuilt message free of reasoning_content.

    When every chunk's ``delta.reasoning_content`` is absent or empty,
    the helper must not synthesise an empty ``reasoning_content`` key
    so consumers can rely on its presence as a positive signal.
    """

    def _plain_chunk(
        content: str, finish_reason: str | None = None
    ) -> SimpleNamespace:
        """Build a chunk stand-in whose reasoning_content stays None."""
        delta = SimpleNamespace(content=content, reasoning_content=None)
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        return SimpleNamespace(
            choices=[choice],
            model_dump=lambda: {"id": "chatcmpl-plain"},
        )

    chunks = [
        _plain_chunk("Hello "),
        _plain_chunk("world.", finish_reason="stop"),
    ]

    async def fake_stream() -> Any:
        """Async generator yielding the prepared non-reasoner chunks."""
        for chunk in chunks:
            yield chunk

    stream_to_dict = getattr(chat_agents, "_stream_response_to_dict")
    result = await stream_to_dict(fake_stream())

    message = result["choices"][0]["message"]
    assert message["content"] == "Hello world."
    assert "reasoning_content" not in message
    assert result["choices"][0]["finish_reason"] == "stop"
