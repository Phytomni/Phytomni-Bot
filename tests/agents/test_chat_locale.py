# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Locale identity and prompt-boundary tests for the shared chat service."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.chat import graph as chat_graph
from mcp_server_phytomni.agents.chat import service as chat_service

pytestmark = pytest.mark.agent


def _cache_call(locale: str) -> chat_service.ChatCacheCall:
    """Build the smallest complete cache call for one locale."""
    return cast(
        chat_service.ChatCacheCall,
        {
            "messages": [{"role": "user", "content": "leaf growth"}],
            "model": "pytest-locale-model",
            "temperature": 0.1,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "n": 1,
            "max_tokens": 64,
            "response_format": {"type": "text"},
            "reasoning_effort": None,
            "api_key": "key",
            "base_url": "https://example.invalid/v1",
            "user": "locale-test",
            "timeout": 1.0,
            "stream": False,
            "locale": locale,
        },
    )


async def test_chat_cache_separates_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same messages in two locales never reuse one answer."""
    chat_service.clear_chat_cache()
    calls = {"count": 0}

    async def fake_create(**_kwargs: Any) -> SimpleNamespace:
        """Count provider calls and return a minimal completion."""
        calls["count"] += 1
        return SimpleNamespace(
            model_dump=lambda: {
                "choices": [
                    {"message": {"content": f"answer-{calls['count']}"}}
                ]
            }
        )

    monkeypatch.setattr(
        chat_service,
        "AsyncOpenAI",
        lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        ),
    )

    await chat_service.run_phyto_chat_cached(**_cache_call("en-US"))
    await chat_service.run_phyto_chat_cached(**_cache_call("zh-CN"))

    assert calls["count"] == 2


async def test_answer_and_follow_up_share_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both chat calls use the persisted locale instruction."""
    captured: list[dict[str, Any]] = []

    def fake_get_prompt(
        _prompt_file: str,
        _prompt_path: str,
        _params: dict[str, Any] | None = None,
    ) -> str:
        """Return a stable prompt without reading the prompt YAML."""
        return "follow-up prompt" if _params is not None else "base prompt"

    async def fake_run(
        messages: list[dict[str, str]], options: dict[str, Any]
    ) -> dict[str, Any]:
        """Capture each provider request and return its turn payload."""
        captured.append({"messages": messages, "options": options})
        content = "main answer" if len(captured) == 1 else '["next"]'
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(chat_service, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chat_graph, "_run_phyto_chat", fake_run)

    result = await chat_service.phyto_chat_with_follow(
        user_query="解释这个基因",
        prompt_file="prompts.yaml",
        prompt_path="system/chat",
        api_key="api-key",
        base_url="https://example.invalid/v1",
        model="pytest-locale-model",
        max_retries=0,
        locale="zh-CN",
    )

    assert result["choices"][0]["message"]["follow_up_questions"] == ["next"]
    assert len(captured) == 2
    for call in captured:
        assert call["options"]["locale"] == "zh-CN"
        assert "Simplified Chinese" in call["options"]["locale_instruction"]
        assert "Simplified Chinese" in call["messages"][0]["content"]
