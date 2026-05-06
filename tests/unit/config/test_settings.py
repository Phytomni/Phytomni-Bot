# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for sensitive settings loading in offline mode."""

# pylint: disable=missing-function-docstring

import pytest

from mcp_server_phytomni.config import settings

pytestmark = pytest.mark.unit


def test_sensitive_config_load_uses_environment_without_real_env_file(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv("API_KEY", "override-api-key")

    config = settings.SensitiveConfig.load()

    assert config.API_KEY.get_secret_value() == "override-api-key"
    assert config.USER_NAME == "pytest-user"
    assert config.USER_PASSWORD.get_secret_value() == "pytest-password"
    assert config.ACCESS_KEY_ID.get_secret_value() == "pytest-access-key-id"
    assert (
        config.SECRET_ACCESS_KEY.get_secret_value()
        == "pytest-secret-access-key"
    )


def test_sensitive_config_masks_secret_repr():
    config = settings.SensitiveConfig.load()

    assert "pytest-api-key" not in repr(config)
    assert "**********" in repr(config)


def test_sensitive_config_prefers_uppercase_obs_env(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY_ID", "uppercase-access-key-id")
    monkeypatch.setenv("SECRET_ACCESS_KEY", "uppercase-secret-access-key")
    monkeypatch.setenv("AccessKeyID", "legacy-access-key-id")
    monkeypatch.setenv("SecretAccessKey", "legacy-secret-access-key")

    config = settings.SensitiveConfig.load()

    assert config.ACCESS_KEY_ID.get_secret_value() == (
        "uppercase-access-key-id"
    )
    assert config.SECRET_ACCESS_KEY.get_secret_value() == (
        "uppercase-secret-access-key"
    )


def test_sensitive_config_accepts_legacy_obs_env(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("AccessKeyID", "legacy-access-key-id")
    monkeypatch.setenv("SecretAccessKey", "legacy-secret-access-key")

    config = settings.SensitiveConfig.load()

    assert config.ACCESS_KEY_ID.get_secret_value() == "legacy-access-key-id"
    assert (
        config.SECRET_ACCESS_KEY.get_secret_value()
        == "legacy-secret-access-key"
    )
