# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Relay-mode routing tests for chat LLM and analyst coder/embed YAML.

Pins that customer relay mode overrides the chat LLM endpoint at the
AsyncOpenAI construction site (even when a caller passes operator
credentials) and rewrites the analyst coder/embed model YAML to the
relay routes with the relay key, while normal mode keeps the operator
endpoints and never leaks operator coder/embed secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst.model_yaml import build_model_yaml
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.config.models.agents import ReviewConfig
from mcp_server_phytomni.config.settings import get_sensitive_config

pytestmark = pytest.mark.agent


@dataclass(frozen=True)
class _FakeCompletion:
    """Frozen Chat Completions stand-in for the model_dump boundary."""

    payload: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        """Return the provider-shaped payload consumed by chat service."""
        return self.payload


def _capturing_async_openai(captured: dict[str, Any]):
    """Return a fake AsyncOpenAI recording the api_key / base_url it gets."""

    async def fake_create(**kwargs: Any) -> _FakeCompletion:
        captured["completion"] = kwargs
        return _FakeCompletion(
            payload={
                "choices": [
                    {"message": {"content": "ok", "role": "assistant"}}
                ]
            }
        )

    def fake_async_openai(api_key: str, base_url: str) -> SimpleNamespace:
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )

    return fake_async_openai


@pytest.mark.parametrize("relay_enabled", [False, True])
async def test_review_timeout_reaches_direct_and_relay_provider(
    monkeypatch: pytest.MonkeyPatch,
    relay_enabled: bool,
) -> None:
    """Review keeps its 30000-second timeout through either transport."""
    chat_service.clear_chat_cache()
    get_sensitive_config.cache_clear()
    try:
        if relay_enabled:
            monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
            monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
            monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-key")
        else:
            monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
            monkeypatch.delenv("RELAY_MODE", raising=False)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            chat_service, "AsyncOpenAI", _capturing_async_openai(captured)
        )

        sampling = _sampling("review timeout transport")
        sampling["timeout"] = ReviewConfig().TIMEOUT
        result = await chat_service.run_phyto_chat_cached(
            api_key="operator-key",
            base_url="https://operator.invalid/v1",
            **sampling,
        )

        assert result["choices"][0]["message"]["content"] == "ok"
        assert captured["completion"]["timeout"] == 30000.0
        if relay_enabled:
            assert captured["base_url"] == "https://relay.test/v1/relay/llm"
        else:
            assert captured["base_url"] == "https://operator.invalid/v1"
    finally:
        chat_service.clear_chat_cache()
        get_sensitive_config.cache_clear()


def _sampling(content: str) -> dict[str, Any]:
    """Return run_phyto_chat_cached sampling kwargs with given content.

    Distinct content per test keeps the func-cache key (which excludes
    api_key / base_url) from sharing a stored payload across tests, so
    the body re-runs and AsyncOpenAI is constructed each time.
    """
    # Sampling values are deliberately non-default (and differ from the
    # other chat tests' bags) so this fixture does not form a
    # duplicate-code (R0801) block with them; they do not affect the
    # api_key / base_url override these tests assert.
    return {
        "messages": [{"role": "user", "content": content}],
        "model": "relay-model",
        "temperature": 0.5,
        "top_p": 0.9,
        "frequency_penalty": 0.1,
        "presence_penalty": 0.2,
        "n": 2,
        "max_tokens": 128,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
        "user": "relay-user",
        "timeout": 2.0,
        "stream": False,
    }


async def test_chat_relay_mode_overrides_llm_endpoint(monkeypatch):
    """Relay mode points AsyncOpenAI at the relay LLM route + relay key.

    Even though the call passes operator credentials, the relay branch
    must override both so the child Bot hits ``/v1/relay/llm`` with the
    relay key rather than the operator LLM directly.
    """
    chat_service.clear_chat_cache()
    get_sensitive_config.cache_clear()
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-key")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        chat_service, "AsyncOpenAI", _capturing_async_openai(captured)
    )

    result = await chat_service.run_phyto_chat_cached(
        api_key="operator-key",
        base_url="https://operator.invalid/v1",
        **_sampling("relay routing"),
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["api_key"] == "relay-key"
    assert captured["base_url"] == "https://relay.test/v1/relay/llm"


async def test_chat_normal_mode_keeps_operator_endpoint(monkeypatch):
    """Outside relay mode the caller's operator endpoint passes through."""
    chat_service.clear_chat_cache()
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        chat_service, "AsyncOpenAI", _capturing_async_openai(captured)
    )

    result = await chat_service.run_phyto_chat_cached(
        api_key="operator-key",
        base_url="https://operator.invalid/v1",
        **_sampling("normal routing"),
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["api_key"] == "operator-key"
    assert captured["base_url"] == "https://operator.invalid/v1"


def _model_configs() -> tuple[Any, Any]:
    """Return (sensitive_config, analyst_config) stubs for the YAML build."""
    sensitive = SimpleNamespace(
        CODER_MODEL="coder-m",
        CODER_URL="https://coder.operator/v1",
        CODER_API_KEY=SimpleNamespace(get_secret_value=lambda: "coder-secret"),
        EMBED_MODEL="embed-m",
        EMBED_URL="https://embed.operator/v1",
        EMBED_API_KEY=SimpleNamespace(get_secret_value=lambda: "embed-secret"),
        RELAY_API_KEY=SimpleNamespace(get_secret_value=lambda: "relay-key"),
    )
    analyst = SimpleNamespace(RELAY_BASE_URL="https://relay.test")
    return sensitive, analyst


def test_model_yaml_normal_mode_uses_operator_endpoints(monkeypatch):
    """Normal mode writes operator coder/embed URLs and keys to the YAML."""
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    sensitive, analyst = _model_configs()

    yaml_text = build_model_yaml(sensitive, analyst)

    assert "api_base: https://coder.operator/v1" in yaml_text
    assert "api_key: coder-secret" in yaml_text
    assert "inference_url: https://embed.operator/v1" in yaml_text
    assert "api_token: embed-secret" in yaml_text


def test_model_yaml_relay_mode_uses_relay_routes(monkeypatch):
    """Relay mode rewrites coder/embed to relay routes + the relay key.

    Operator coder/embed secrets must never appear in the YAML the
    remote analysis platform receives.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    sensitive, analyst = _model_configs()

    yaml_text = build_model_yaml(sensitive, analyst)

    assert "api_base: https://relay.test/v1/relay/coder" in yaml_text
    assert "inference_url: https://relay.test/v1/relay/embed" in yaml_text
    assert "api_key: relay-key" in yaml_text
    assert "api_token: relay-key" in yaml_text
    assert "coder-secret" not in yaml_text
    assert "embed-secret" not in yaml_text
    # Model identifiers stay operator-provided in both modes.
    assert "model_name: coder-m" in yaml_text
    assert "model_id: embed-m" in yaml_text
