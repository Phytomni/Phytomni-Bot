# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP API non-secret configuration.

Covers ApiConfig default values and environment-variable overrides for the
SQLite store paths and rate-limit / TTL knobs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config.defaults import ApiConfig

pytestmark = pytest.mark.unit

_CACHE_DIR = Path(".cache") / "phytomni"


def test_api_config_defaults() -> None:
    """Verify ApiConfig ships sane local-only defaults."""
    config = ApiConfig()

    assert config.API_HOST == "127.0.0.1"
    assert config.API_PORT == 8080
    assert str(_CACHE_DIR / "api_keys.sqlite") == config.API_KEYS_DB_PATH
    assert config.API_TASKS_DB_PATH == "server_tasks.db"
    assert not hasattr(config, "API_RUNS_DB_PATH")
    assert config.MEMORY_MAX_ITEMS == 100
    assert config.MEMORY_MAX_CONTENT_BYTES == 16 * 1024
    assert config.MEMORY_MAX_TOTAL_BYTES == 1024 * 1024
    assert config.MEMORY_MAX_RETRIEVAL == 20
    assert config.MEMORY_GRAPH_MAX_BYTES == 64 * 1024
    assert config.API_REQUEST_TIMEOUT == 600.0
    assert config.API_RATE_LIMIT_PER_MIN == 120
    assert config.API_RUN_TTL_OK_HOURS == 24
    assert config.API_RUN_TTL_FAIL_DAYS == 7
    assert config.API_SERVICE_TOKEN is None
    assert config.API_UPLOAD_MAX_BYTES == 26_214_400
    assert config.API_UPLOAD_PREFIX == "agent_data/uploads"
    assert config.STREAM_ANSWER_MAX_BYTES == 1_048_576
    assert config.CONVERSATION_CONTEXT_V1_ENABLED is False
    assert config.CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET == 6_000
    assert config.CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET == 4_000
    assert config.CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET == 3_000
    assert config.CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET == 6_000
    assert config.CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET == 4_000
    assert config.A2UI_ENABLED is False
    assert config.A2UI_TOOL_CALL is False
    assert config.A2A_ENABLED is False
    assert config.A2A_PUBLIC_BASE_URL is None
    assert config.INTEROP_MAX_TARGETS == 64
    assert config.INTEROP_CACHE_MAX_ENTRIES == 256
    assert config.A2A_MAX_HISTORY_MESSAGES == 32
    assert config.A2A_MAX_ARTIFACT_BYTES == 256 * 1024
    assert config.A2UI_MAX_BODY_BYTES == 65_536
    assert config.A2UI_MAX_RESPONSE_BYTES == 1_048_576
    assert config.A2UI_MAX_IDENTIFIER_RUNES == 256
    assert config.A2UI_MAX_FORM_FIELDS == 20
    assert config.A2UI_MAX_SCALAR_CHARS == 4096
    assert config.A2UI_MAX_CHOICES == 100
    assert config.RELAY_ENABLED is False
    assert str(_CACHE_DIR / "relay_audit.sqlite") == config.RELAY_AUDIT_DB_PATH
    assert config.RELAY_AUDIT_RETENTION_DAYS == 90
    assert config.RELAY_REQUEST_MAX_BYTES == 10_485_760
    assert config.RELAY_REQUEST_AUDIT_MAX_BYTES == 65_536
    assert config.RELAY_TIMEOUT_SECONDS == 600.0
    assert config.RELAY_RESPONSE_AUDIT_MAX_BYTES == 10_485_760
    assert config.RELAY_RESPONSE_MAX_BYTES == 1024 * 1024 * 1024
    assert config.RELAY_RATE_LIMIT_PER_MIN == 60
    assert config.RELAY_MAX_CONCURRENT_PER_KEY == 8


def test_api_config_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PHYTOMNI_-prefixed env vars override store paths."""
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", "/tmp/keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", "/tmp/tasks.sqlite")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "5")

    config = ApiConfig()

    assert config.API_KEYS_DB_PATH == "/tmp/keys.sqlite"
    assert config.API_TASKS_DB_PATH == "/tmp/tasks.sqlite"
    assert config.API_RATE_LIMIT_PER_MIN == 5


def test_stream_answer_max_bytes_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_STREAM_ANSWER_MAX_BYTES overrides the soft cap."""
    monkeypatch.setenv("PHYTOMNI_STREAM_ANSWER_MAX_BYTES", "4096")
    config = ApiConfig()
    assert config.STREAM_ANSWER_MAX_BYTES == 4096


def test_conversation_context_config_uses_prefixed_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conversation context remains disabled until explicitly enabled."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET", "512"
    )
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET", "16000"
    )
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET", "513"
    )
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET", "514"
    )
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET", "515"
    )

    config = ApiConfig()

    assert config.CONVERSATION_CONTEXT_V1_ENABLED is True
    assert config.CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET == 512
    assert config.CONVERSATION_CONTEXT_KNOWLEDGE_TOKEN_BUDGET == 16_000
    assert config.CONVERSATION_CONTEXT_DATA_TOKEN_BUDGET == 513
    assert config.CONVERSATION_CONTEXT_REVIEW_TOKEN_BUDGET == 514
    assert config.CONVERSATION_CONTEXT_BRIEF_GENE_TOKEN_BUDGET == 515


@pytest.mark.parametrize(
    "value",
    ["511", "16001"],
)
def test_conversation_context_token_budgets_reject_unsafe_bounds(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Token budgets cannot be set outside the bounded context window."""
    monkeypatch.setenv(
        "PHYTOMNI_CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET", value
    )

    with pytest.raises(
        ValidationError,
        match="CONVERSATION_CONTEXT_CHAT_TOKEN_BUDGET",
    ):
        ApiConfig()


def test_memory_and_interop_limits_accept_prefixed_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded stores and registries use explicit operator knobs."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_ITEMS", "12")
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_CONTENT_BYTES", "2048")
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_TOTAL_BYTES", "8192")
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_RETRIEVAL", "5")
    monkeypatch.setenv("PHYTOMNI_MEMORY_GRAPH_MAX_BYTES", "4096")
    monkeypatch.setenv("PHYTOMNI_INTEROP_MAX_TARGETS", "7")
    monkeypatch.setenv("PHYTOMNI_INTEROP_CACHE_MAX_ENTRIES", "9")
    monkeypatch.setenv("PHYTOMNI_A2A_MAX_HISTORY_MESSAGES", "3")
    monkeypatch.setenv("PHYTOMNI_A2A_MAX_ARTIFACT_BYTES", "2048")

    config = ApiConfig()

    assert config.MEMORY_MAX_ITEMS == 12
    assert config.MEMORY_MAX_CONTENT_BYTES == 2048
    assert config.MEMORY_MAX_TOTAL_BYTES == 8192
    assert config.MEMORY_MAX_RETRIEVAL == 5
    assert config.MEMORY_GRAPH_MAX_BYTES == 4096
    assert config.INTEROP_MAX_TARGETS == 7
    assert config.INTEROP_CACHE_MAX_ENTRIES == 9
    assert config.A2A_MAX_HISTORY_MESSAGES == 3
    assert config.A2A_MAX_ARTIFACT_BYTES == 2048


def test_memory_retrieval_limit_cannot_exceed_item_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retrieval bound larger than namespace capacity fails closed."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_ITEMS", "2")
    monkeypatch.setenv("PHYTOMNI_MEMORY_MAX_RETRIEVAL", "3")

    with pytest.raises(ValidationError, match="MEMORY_MAX_RETRIEVAL"):
        ApiConfig()


def test_a2ui_flags_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_A2UI_* env vars flip the A2UI feature flags."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    monkeypatch.setenv("PHYTOMNI_A2UI_TOOL_CALL", "1")
    config = ApiConfig()
    assert config.A2UI_ENABLED is True
    assert config.A2UI_TOOL_CALL is True


def test_a2ui_limits_accept_prefixed_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2UI shape limits accept their PHYTOMNI aliases."""
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_BODY_BYTES", "32768")
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_RESPONSE_BYTES", "524288")
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_IDENTIFIER_RUNES", "128")
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_FORM_FIELDS", "10")
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_SCALAR_CHARS", "2048")
    monkeypatch.setenv("PHYTOMNI_A2UI_MAX_CHOICES", "50")

    config = ApiConfig()

    assert config.A2UI_MAX_BODY_BYTES == 32768
    assert config.A2UI_MAX_RESPONSE_BYTES == 524288
    assert config.A2UI_MAX_IDENTIFIER_RUNES == 128
    assert config.A2UI_MAX_FORM_FIELDS == 10
    assert config.A2UI_MAX_SCALAR_CHARS == 2048
    assert config.A2UI_MAX_CHOICES == 50


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("A2UI_MAX_BODY_BYTES", "65537"),
        ("A2UI_MAX_RESPONSE_BYTES", "1048577"),
        ("A2UI_MAX_IDENTIFIER_RUNES", "257"),
        ("A2UI_MAX_FORM_FIELDS", "21"),
        ("A2UI_MAX_SCALAR_CHARS", "4097"),
        ("A2UI_MAX_CHOICES", "101"),
    ],
)
def test_a2ui_limits_reject_unsafe_upper_bounds(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    value: str,
) -> None:
    """Operators cannot widen the Web-compatible A2UI safety boundary."""
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValidationError, match=env_name):
        ApiConfig()


def test_a2a_config_env_aliases_and_url_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2A flags accept both aliases and normalize the public base URL."""
    monkeypatch.setenv("PHYTOMNI_A2A_ENABLED", "true")
    monkeypatch.setenv("A2A_PUBLIC_BASE_URL", "https://agent.example/base///")

    config = ApiConfig()

    assert config.A2A_ENABLED is True
    assert config.A2A_PUBLIC_BASE_URL == "https://agent.example/base"


@pytest.mark.parametrize(
    "value",
    [
        "ftp://agent.example",
        "/relative/path",
        "https://",
        "https://user:pass@agent.example",
        "https://agent.example?tenant=1",
        "https://agent.example/#fragment",
    ],
)
def test_a2a_public_base_url_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The public URL must be an absolute credential-free HTTP(S) URL."""
    monkeypatch.setenv("A2A_PUBLIC_BASE_URL", value)

    with pytest.raises(ValidationError, match="A2A_PUBLIC_BASE_URL"):
        ApiConfig()


def test_a2a_enabled_requires_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling A2A without its public URL fails during settings load."""
    monkeypatch.setenv("A2A_ENABLED", "1")
    monkeypatch.delenv("A2A_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("PHYTOMNI_A2A_PUBLIC_BASE_URL", raising=False)

    with pytest.raises(ValidationError, match="A2A_PUBLIC_BASE_URL"):
        ApiConfig()


def test_api_service_token_loads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify API_SERVICE_TOKEN env populates the field as a SecretStr."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "svc-secret-123")

    config = ApiConfig()

    assert config.API_SERVICE_TOKEN is not None
    assert config.API_SERVICE_TOKEN.get_secret_value() == "svc-secret-123"


def test_api_service_token_repr_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SecretStr wrapping keeps the token out of repr / model_dump."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "do-not-print-this")

    config = ApiConfig()

    rendered = repr(config)
    dumped = str(config.model_dump())
    assert "do-not-print-this" not in rendered
    assert "do-not-print-this" not in dumped


def test_relay_config_prefixed_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PHYTOMNI_RELAY_-prefixed env vars override relay knobs."""
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_RELAY_AUDIT_DB_PATH", "/tmp/relay_audit.sqlite"
    )
    monkeypatch.setenv("PHYTOMNI_RELAY_AUDIT_RETENTION_DAYS", "30")
    monkeypatch.setenv("PHYTOMNI_RELAY_REQUEST_MAX_BYTES", "2048")
    monkeypatch.setenv("PHYTOMNI_RELAY_REQUEST_AUDIT_MAX_BYTES", "1024")
    monkeypatch.setenv("PHYTOMNI_RELAY_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("PHYTOMNI_RELAY_RESPONSE_AUDIT_MAX_BYTES", "4096")
    monkeypatch.setenv("PHYTOMNI_RELAY_RESPONSE_MAX_BYTES", "8192")
    monkeypatch.setenv("PHYTOMNI_RELAY_RATE_LIMIT_PER_MIN", "15")
    monkeypatch.setenv("PHYTOMNI_RELAY_MAX_CONCURRENT_PER_KEY", "3")

    config = ApiConfig()

    assert config.RELAY_ENABLED is True
    assert config.RELAY_AUDIT_DB_PATH == "/tmp/relay_audit.sqlite"
    assert config.RELAY_AUDIT_RETENTION_DAYS == 30
    assert config.RELAY_REQUEST_MAX_BYTES == 2048
    assert config.RELAY_REQUEST_AUDIT_MAX_BYTES == 1024
    assert config.RELAY_TIMEOUT_SECONDS == 12.5
    assert config.RELAY_RESPONSE_AUDIT_MAX_BYTES == 4096
    assert config.RELAY_RESPONSE_MAX_BYTES == 8192
    assert config.RELAY_RATE_LIMIT_PER_MIN == 15
    assert config.RELAY_MAX_CONCURRENT_PER_KEY == 3


def test_relay_config_unprefixed_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the unprefixed RELAY_ aliases also override relay knobs."""
    monkeypatch.setenv("RELAY_ENABLED", "true")
    monkeypatch.setenv("RELAY_AUDIT_RETENTION_DAYS", "7")
    monkeypatch.setenv("RELAY_REQUEST_AUDIT_MAX_BYTES", "2048")
    monkeypatch.setenv("RELAY_RATE_LIMIT_PER_MIN", "9")
    monkeypatch.setenv("RELAY_MAX_CONCURRENT_PER_KEY", "2")
    monkeypatch.setenv("RELAY_RESPONSE_MAX_BYTES", "65536")

    config = ApiConfig()

    assert config.RELAY_ENABLED is True
    assert config.RELAY_AUDIT_RETENTION_DAYS == 7
    assert config.RELAY_REQUEST_AUDIT_MAX_BYTES == 2048
    assert config.RELAY_RATE_LIMIT_PER_MIN == 9
    assert config.RELAY_MAX_CONCURRENT_PER_KEY == 2
    assert config.RELAY_RESPONSE_MAX_BYTES == 65536
