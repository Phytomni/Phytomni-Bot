# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for ChatAgent service helpers."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni import chat_agents

pytestmark = pytest.mark.agent


class FakeChatCompletion:
    """Small OpenAI response stand-in with the model_dump contract."""

    def __init__(self, content: str = "ok"):
        self.content = content

    def model_dump(self) -> dict[str, Any]:
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


async def test_phyto_chat_converts_uploads_and_builds_openai_request(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    async def fake_download_list_convert(**kwargs: Any) -> list[str]:
        captured["download"] = kwargs
        return ["converted paper text"]

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        captured["prompt"] = {
            "prompt_file": prompt_file,
            "prompt_path": prompt_path,
            "params": params,
        }
        return "system prompt"

    class FakeCompletions:
        """Capture OpenAI completion kwargs."""

        async def create(self, **kwargs: Any) -> FakeChatCompletion:
            captured["completion"] = kwargs
            return FakeChatCompletion("chat answer")

    class FakeAsyncOpenAI:
        """Minimal AsyncOpenAI-compatible client."""

        def __init__(self, api_key: str, base_url: str):
            captured["client"] = {
                "api_key": api_key,
                "base_url": base_url,
            }
            self.chat = type(
                "FakeChat",
                (),
                {"completions": FakeCompletions()},
            )()

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
    assert "reasoning_effort" not in captured["completion"]


async def test_phyto_chat_with_follow_attaches_follow_up_questions(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[dict[str, Any]] = []

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
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
