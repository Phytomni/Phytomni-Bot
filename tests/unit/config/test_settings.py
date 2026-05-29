# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for sensitive settings loading in offline mode.

Covers environment-only loading, secret repr masking, uppercase OBS variables,
and legacy OBS environment variable fallback.
"""

from typing import Any, cast

import pytest

from mcp_server_phytomni.config import settings

pytestmark = pytest.mark.unit


def test_sensitive_config_load_uses_environment_without_real_env_file(
    monkeypatch,
    tmp_path,
):
    """Verify SensitiveConfig loads environment without a real env file.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to adjust env/path state.
        tmp_path: Temporary directory used for a missing .env path.
    """
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


def test_sensitive_config_ignores_server_config_dotenv_keys(tmp_path):
    """Verify SensitiveConfig ignores ServerConfig keys in the .env file.

    The deployment endpoints (RETRIEVE_URL, APP_ID, ...) live in the same
    shared .env that SensitiveConfig parses directly via ``env_file``.
    pydantic forbids unknown dotenv keys by default, so without
    ``extra="ignore"`` these ServerConfig-owned keys raise
    ``extra_forbidden``. The secret fields themselves are still supplied
    by the test environment (os.environ wins over the dotenv source).

    Args:
        tmp_path: Temporary directory used for the shared-style .env file.
    """
    shared_env = tmp_path / "shared.env"
    shared_env.write_text(
        "RETRIEVE_URL=http://example.invalid/search\n"
        'APP_ID={"small":"x"}\n',
        encoding="utf-8",
    )

    # Mirror get_sensitive_config: pydantic-settings accepts the
    # special _env_file init arg at runtime, but the synthesized
    # __init__ that type checkers see does not, so call through Any.
    settings_cls = cast(Any, settings.SensitiveConfig)
    config = settings_cls(_env_file=str(shared_env))

    assert config.USER_NAME == "pytest-user"
    assert not hasattr(config, "RETRIEVE_URL")
    assert not hasattr(config, "APP_ID")


def test_sensitive_config_masks_secret_repr():
    """Verify sensitive config masks secret repr."""
    config = settings.SensitiveConfig.load()

    assert "pytest-api-key" not in repr(config)
    assert "**********" in repr(config)


def test_sensitive_config_prefers_uppercase_obs_env(monkeypatch):
    """Verify SensitiveConfig prefers uppercase OBS env.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to set env vars.
    """
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
    """Verify SensitiveConfig accepts legacy OBS env.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to set env vars.
    """
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
