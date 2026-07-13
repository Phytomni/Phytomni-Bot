# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for sensitive settings loading in offline mode.

Covers environment-only loading, secret repr masking, uppercase OBS variables,
and legacy OBS environment variable fallback.
"""

import os
import subprocess
import sys
from typing import Any, cast

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config import settings

pytestmark = pytest.mark.unit

# Required (no-default) secret fields that relay mode must make optional.
# Each entry is (field_name, *env_aliases) so the relay-boot test can
# strip every source the value could resolve from.
_REQUIRED_SECRET_ENV = (
    ("DOMAIN_NAME",),
    ("USER_NAME",),
    ("USER_PASSWORD",),
    ("ACCESS_KEY_ID", "AccessKeyID"),
    ("SECRET_ACCESS_KEY", "SecretAccessKey"),
    ("BASE_URL",),
    ("MODEL_ID",),
    ("API_KEY",),
    ("CODER_URL",),
    ("CODER_MODEL",),
    ("CODER_API_KEY",),
    ("EMBED_URL",),
    ("EMBED_MODEL",),
    ("EMBED_API_KEY",),
)


def _strip_operator_secrets(monkeypatch):
    """Remove every operator secret env var and its aliases."""
    for field, *aliases in _REQUIRED_SECRET_ENV:
        monkeypatch.delenv(field, raising=False)
        monkeypatch.delenv(f"PHYTOMNI_{field}", raising=False)
        for alias in aliases:
            monkeypatch.delenv(alias, raising=False)


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


def test_interop_credentials_are_lazy_secret_json(monkeypatch):
    """Interop credentials stay opaque and masked in SensitiveConfig."""
    secret_json = '{"peer-auth":{"authorization":"Bearer hidden"}}'
    monkeypatch.setenv("PHYTOMNI_INTEROP_CREDENTIALS", secret_json)

    config = settings.SensitiveConfig.load()

    assert config.INTEROP_CREDENTIALS.get_secret_value() == secret_json
    assert "Bearer hidden" not in repr(config)


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


def test_relay_mode_boots_without_operator_secrets(monkeypatch):
    """Relay mode makes the operator secret fields optional.

    A customer child Bot ships only ``PHYTOMNI_RELAY_*``; it never
    receives the operator IAM / OBS / model credentials. With relay
    mode enabled, ``SensitiveConfig`` must construct cleanly even though
    all 14 normally-required secrets are absent, so the many modules
    that build it at import time do not raise during a relay-mode boot.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-secret-value")
    _strip_operator_secrets(monkeypatch)

    settings_cls = cast(Any, settings.SensitiveConfig)
    config = settings_cls(_env_file=None)

    assert config.RELAY_API_KEY.get_secret_value() == "relay-secret-value"
    assert config.API_KEY.get_secret_value() == ""
    assert config.BASE_URL == ""


def test_normal_mode_missing_secret_still_raises(monkeypatch):
    """Outside relay mode a missing secret still fails fast.

    Pins that the relay fork does not weaken normal-mode validation:
    the operator deployment must still raise when a credential is
    absent rather than silently constructing with empty secrets.
    """
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("PHYTOMNI_API_KEY", raising=False)

    settings_cls = cast(Any, settings.SensitiveConfig)
    with pytest.raises(ValidationError) as excinfo:
        settings_cls(_env_file=None)

    assert "API_KEY" in str(excinfo.value)


def test_relay_api_key_is_secret_and_redacted(monkeypatch):
    """``RELAY_API_KEY`` is a ``SecretStr`` masked in repr/model dumps."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-secret-value")
    _strip_operator_secrets(monkeypatch)

    settings_cls = cast(Any, settings.SensitiveConfig)
    config = settings_cls(_env_file=None)

    assert "relay-secret-value" not in repr(config)
    assert "**********" in repr(config)


def test_relay_api_key_defaults_empty_outside_relay():
    """``RELAY_API_KEY`` defaults to an empty secret in normal mode."""
    config = settings.SensitiveConfig.load()

    assert config.RELAY_API_KEY.get_secret_value() == ""


def test_relay_mode_imports_config_building_module_without_secrets():
    """A module that builds a config at import time boots in relay mode.

    The decisive S1 guarantee: ``storage/uploads.py`` constructs
    ``ServerConfig()`` at module scope, so a customer relay image
    importing the package with ONLY ``PHYTOMNI_RELAY_*`` set (no
    operator endpoints, no secrets) must not raise ``ValidationError``
    during import. Run the import in a clean subprocess so the
    operator env the test session carries cannot mask the relax.
    """
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PHYTOMNI_TESTING": "1",
        "PHYTOMNI_RELAY_MODE": "1",
        "PHYTOMNI_RELAY_BASE_URL": "https://relay.test",
        "PHYTOMNI_RELAY_API_KEY": "relay-secret-value",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import mcp_server_phytomni.storage.uploads"],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
