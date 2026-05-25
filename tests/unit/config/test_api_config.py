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
    assert config.API_KEYS_DB_PATH == str(_CACHE_DIR / "api_keys.sqlite")
    assert config.API_TASKS_DB_PATH == "server_tasks.db"
    assert not hasattr(config, "API_RUNS_DB_PATH")
    assert config.API_REQUEST_TIMEOUT == 600.0
    assert config.API_RATE_LIMIT_PER_MIN == 120
    assert config.API_RUN_TTL_OK_HOURS == 24
    assert config.API_RUN_TTL_FAIL_DAYS == 7
    assert config.API_SERVICE_TOKEN is None
    assert config.API_UPLOAD_MAX_BYTES == 26_214_400
    assert config.API_UPLOAD_PREFIX == "agent_data/uploads"


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
