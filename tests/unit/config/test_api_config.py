# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP API non-secret configuration.

Covers ApiConfig default values and environment-variable overrides for the
SQLite store paths and rate-limit / TTL knobs.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.config.defaults import ApiConfig

pytestmark = pytest.mark.unit


def test_api_config_defaults() -> None:
    """Verify ApiConfig ships sane local-only defaults."""
    config = ApiConfig()

    assert config.API_HOST == "127.0.0.1"
    assert config.API_PORT == 8080
    assert config.API_KEYS_DB_PATH.endswith("api_keys.sqlite")
    assert config.API_RUNS_DB_PATH.endswith("api_runs.sqlite")
    assert config.API_TASKS_DB_PATH == "server_tasks.db"
    assert config.API_REQUEST_TIMEOUT == 600.0
    assert config.API_RATE_LIMIT_PER_MIN == 120
    assert config.API_RUN_TTL_OK_HOURS == 24
    assert config.API_RUN_TTL_FAIL_DAYS == 7


def test_api_config_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PHYTOMNI_-prefixed env vars override store paths."""
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", "/tmp/keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_RUNS_DB", "/tmp/runs.sqlite")
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "5")

    config = ApiConfig()

    assert config.API_KEYS_DB_PATH == "/tmp/keys.sqlite"
    assert config.API_RUNS_DB_PATH == "/tmp/runs.sqlite"
    assert config.API_RATE_LIMIT_PER_MIN == 5
