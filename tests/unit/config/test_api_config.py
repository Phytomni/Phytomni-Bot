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
    assert config.API_REQUEST_TIMEOUT == 600.0
    assert config.API_RATE_LIMIT_PER_MIN == 120
    assert config.API_RUN_TTL_OK_HOURS == 24
    assert config.API_RUN_TTL_FAIL_DAYS == 7
    assert config.API_SERVICE_TOKEN is None
    assert config.API_UPLOAD_MAX_BYTES == 26_214_400
    assert config.API_UPLOAD_PREFIX == "agent_data/uploads"
    assert config.STREAM_ANSWER_MAX_BYTES == 1_048_576
    assert config.A2UI_ENABLED is False
    assert config.A2UI_TOOL_CALL is False
    assert config.RELAY_ENABLED is False
    assert str(_CACHE_DIR / "relay_audit.sqlite") == config.RELAY_AUDIT_DB_PATH
    assert config.RELAY_AUDIT_RETENTION_DAYS == 90
    assert config.RELAY_REQUEST_MAX_BYTES == 10_485_760
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


def test_a2ui_flags_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHYTOMNI_A2UI_* env vars flip the A2UI feature flags."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    monkeypatch.setenv("PHYTOMNI_A2UI_TOOL_CALL", "1")
    config = ApiConfig()
    assert config.A2UI_ENABLED is True
    assert config.A2UI_TOOL_CALL is True


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
    monkeypatch.setenv("RELAY_RATE_LIMIT_PER_MIN", "9")
    monkeypatch.setenv("RELAY_MAX_CONCURRENT_PER_KEY", "2")
    monkeypatch.setenv("RELAY_RESPONSE_MAX_BYTES", "65536")

    config = ApiConfig()

    assert config.RELAY_ENABLED is True
    assert config.RELAY_AUDIT_RETENTION_DAYS == 7
    assert config.RELAY_RATE_LIMIT_PER_MIN == 9
    assert config.RELAY_MAX_CONCURRENT_PER_KEY == 2
    assert config.RELAY_RESPONSE_MAX_BYTES == 65536
