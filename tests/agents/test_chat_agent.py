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
    """Verify identical sampling inputs hit the LLM endpoint once.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace AsyncOpenAI.

    Returns:
        None after cache hit assertions pass.
    """
    chat_agents.run_phyto_chat_cached.cache_clear()
    calls = {"create": 0}

    async def fake_create(**kwargs: Any) -> FakeChatCompletion:
        """Tally each completion request and return a fake response."""
        del kwargs
        calls["create"] += 1
        return FakeChatCompletion(f"answer-{calls['create']}")

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        """Return a SimpleNamespace mimicking AsyncOpenAI(api_key, base_url)."""
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

    # Flipping a semantic input (temperature) must miss the cache and
    # trigger a fresh completion.
    third = await chat_agents.run_phyto_chat_cached(
        **{**sampling_kwargs, "temperature": 0.9}
    )

    assert third != first
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
        """Return a SimpleNamespace mimicking AsyncOpenAI(api_key, base_url)."""
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
