# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for sensitive settings loading in offline mode."""

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


def test_sensitive_config_masks_secret_repr():
    config = settings.SensitiveConfig.load()

    assert "pytest-api-key" not in repr(config)
    assert "**********" in repr(config)
