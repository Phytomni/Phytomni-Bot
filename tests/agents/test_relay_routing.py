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
from mcp_server_phytomni.config.models.agents import (
    BriefGeneConfig,
    ChatConfig,
    DataConfig,
    KnowledgeConfig,
    ReviewConfig,
)
from mcp_server_phytomni.config.settings import get_sensitive_config
from tests.support.outbound_fakes import patch_openai_runtime

pytestmark = pytest.mark.agent

_RELAY_TIMEOUT_PROFILE_HEADER = "X-Phytomni-Relay-Timeout-Profile"


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


def _install_runtime_openai(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
) -> None:
    """Inject one process-owned fake OpenAI client for a service test."""
    factory = _capturing_async_openai(captured)
    patch_openai_runtime(
        monkeypatch,
        chat_service,
        factory(api_key, base_url),
    )


@pytest.mark.parametrize(
    ("config_type", "expected_timeout", "expected_profile"),
    (
        (ChatConfig, 3000.0, "phyto-chat"),
        (KnowledgeConfig, 15000.0, "phyto-knowledge"),
        (DataConfig, 9000.0, "phyto-data"),
        (ReviewConfig, 30000.0, "phyto-review"),
        (BriefGeneConfig, 30000.0, "phyto-brief-gene"),
    ),
)
@pytest.mark.parametrize("relay_enabled", [False, True])
async def test_agent_timeout_reaches_direct_and_relay_provider(
    monkeypatch: pytest.MonkeyPatch,
    relay_enabled: bool,
    config_type: type[ChatConfig],
    expected_timeout: float,
    expected_profile: str,
) -> None:
    """Each synchronous Agent keeps its budget through either transport."""
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
        _install_runtime_openai(
            monkeypatch,
            captured,
            api_key="relay-key" if relay_enabled else "operator-key",
            base_url=(
                "https://relay.test/v1/relay/llm"
                if relay_enabled
                else "https://operator.invalid/v1"
            ),
        )

        sampling = _sampling(
            f"{config_type.__name__} timeout transport {relay_enabled}"
        )
        sampling["timeout"] = config_type().TIMEOUT
        sampling["relay_timeout_profile"] = expected_profile
        result = await chat_service.run_phyto_chat_cached(**sampling)

        assert result["choices"][0]["message"]["content"] == "ok"
        assert captured["completion"]["timeout"] == expected_timeout
        if relay_enabled:
            assert captured["base_url"] == "https://relay.test/v1/relay/llm"
            assert captured["completion"]["extra_headers"] == {
                _RELAY_TIMEOUT_PROFILE_HEADER: expected_profile
            }
        else:
            assert captured["base_url"] == "https://operator.invalid/v1"
            assert "extra_headers" not in captured["completion"]
    finally:
        chat_service.clear_chat_cache()
        get_sensitive_config.cache_clear()


def _sampling(content: str) -> dict[str, Any]:
    """Return run_phyto_chat_cached sampling kwargs with given content.

    Distinct content per test keeps the semantic func-cache key from sharing a
    stored payload across tests, so the process-owned runtime is exercised.
    """
    # Sampling values are deliberately non-default (and differ from the
    # other chat tests' bags) so this fixture does not form a
    # duplicate-code (R0801) block with them.
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


async def test_chat_relay_mode_uses_process_owned_llm_endpoint(monkeypatch):
    """Relay mode uses the process-owned relay LLM route and relay key."""
    chat_service.clear_chat_cache()
    get_sensitive_config.cache_clear()
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-key")
    captured: dict[str, Any] = {}
    _install_runtime_openai(
        monkeypatch,
        captured,
        api_key="relay-key",
        base_url="https://relay.test/v1/relay/llm",
    )

    result = await chat_service.run_phyto_chat_cached(
        **_sampling("relay routing")
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert captured["api_key"] == "relay-key"
    assert captured["base_url"] == "https://relay.test/v1/relay/llm"
    assert "extra_headers" not in captured["completion"]


async def test_chat_normal_mode_uses_process_owned_operator_endpoint(
    monkeypatch,
):
    """Normal mode uses the process-owned operator endpoint."""
    chat_service.clear_chat_cache()
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    captured: dict[str, Any] = {}
    _install_runtime_openai(
        monkeypatch,
        captured,
        api_key="operator-key",
        base_url="https://operator.invalid/v1",
    )

    result = await chat_service.run_phyto_chat_cached(
        **_sampling("normal routing")
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
