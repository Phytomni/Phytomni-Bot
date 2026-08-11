# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for process-owned Chat provider configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.support.config_fakes import (
    fake_chat_config,
    fake_sensitive_config,
)

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.shared.options import build_chat_kwargs

pytestmark = pytest.mark.unit


def _cache_call() -> chat_service.ChatCacheCall:
    """Return one valid typed-cache call without provider ownership fields."""
    return {
        "messages": [{"role": "user", "content": "runtime ownership"}],
        "model": "pytest-model",
        "temperature": 0.2,
        "top_p": 0.9,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "n": 1,
        "max_tokens": None,
        "response_format": {"type": "text"},
        "reasoning_effort": None,
        "user": "pytest-user",
        "timeout": 1.0,
        "stream": False,
    }


@pytest.mark.parametrize("field", ["api_key", "base_url"])
def test_public_option_builders_reject_provider_ownership_override(
    field: str,
) -> None:
    """Per-call credentials and endpoints fail closed at both entry seams."""
    with pytest.raises(TypeError, match=field):
        getattr(chat_service, "_chat_options")({field: "obsolete"})
    with pytest.raises(TypeError, match=field):
        build_chat_kwargs(
            {field: "obsolete"},
            fake_chat_config(),
            fake_sensitive_config(),
        )


@pytest.mark.parametrize("field", ["api_key", "base_url"])
async def test_cache_adapter_rejects_provider_ownership_override(
    field: str,
) -> None:
    """The typed cache seam rejects obsolete provider ownership fields."""
    required_keys = getattr(chat_service.ChatCacheCall, "__required_keys__")
    assert field not in required_keys
    call = dict(_cache_call())
    call[field] = "obsolete"
    with pytest.raises(TypeError, match=field):
        await chat_service.run_phyto_chat_cached(
            **cast(chat_service.ChatCacheCall, call),
        )


async def test_chat_completion_uses_runtime_client_without_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the process-owned client reaches the provider request seam."""
    captured: dict[str, Any] = {}

    async def create(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            model_dump=lambda: {"choices": [{"message": {"content": "ok"}}]}
        )

    @asynccontextmanager
    async def lease(_pool: object) -> AsyncIterator[None]:
        yield

    runtime = SimpleNamespace(
        pools=SimpleNamespace(lease=lease),
        openai=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            )
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "current_outbound_runtime",
        lambda: runtime,
    )
    chat_service.clear_chat_cache()

    result = await chat_service.run_phyto_chat_cached(**_cache_call())

    assert result["choices"][0]["message"]["content"] == "ok"
    assert "api_key" not in captured
    assert "base_url" not in captured
