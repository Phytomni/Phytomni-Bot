# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests that SensitiveConfig.load picks up file-only license keys.

The previous load() implementation called ``os.getenv(LICENSE_KEY_ENV)``
directly and missed the Model A delivery path where the key arrives via
the on-disk ``LICENSE_KEY_PATH`` file instead of the environment. These
tests assert end-to-end success for the file-only, env-only, and
both-set provisioning shapes through ``SensitiveConfig.load``.
"""

import os

import pytest

from mcp_server_phytomni.config import encrypt_env_file, settings

pytestmark = pytest.mark.unit

LICENSE = "customer-license-key-002"
MEMO_PATH = "mcp_server_phytomni.config.settings._ENV_DECRYPT_MEMO"

FULL_ENV = (
    "DOMAIN_NAME=key-domain\n"
    "USER_NAME=key-user\n"
    "USER_PASSWORD=key-password\n"
    "ACCESS_KEY_ID=key-access-key\n"
    "SECRET_ACCESS_KEY=key-secret-key\n"
    "BASE_URL=https://key.example/llm\n"
    "MODEL_ID=key-model\n"
    "API_KEY=key-api-key\n"
    "CODER_URL=https://key.example/coder\n"
    "CODER_MODEL=key-coder-model\n"
    "CODER_API_KEY=key-coder-api-key\n"
    "EMBED_URL=https://key.example/embed\n"
    "EMBED_MODEL=key-embed-model\n"
    "EMBED_API_KEY=key-embed-api-key\n"
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Snapshot os.environ and reset the decrypt memo per test.

    Mirrors the isolation guard in test_sensitive_config_load so the
    blob's os.environ.setdefault writes never bleed into other tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        None. Cleanup restores the captured environment.
    """
    snapshot = dict(os.environ)
    monkeypatch.setattr(MEMO_PATH, {"done": False})
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def _seal(tmp_path, license_key=LICENSE):
    """Write a plaintext .env and return its sealed envelope path.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        license_key: License key used to derive the AES key.

    Returns:
        Path to the written .env.encrypted blob.
    """
    src = tmp_path / ".env"
    src.write_text(FULL_ENV, encoding="utf-8")
    blob = tmp_path / ".env.encrypted"
    encrypt_env_file(src, license_key, blob)
    return blob


def _clear_secret_env_vars():
    """Strip every SensitiveConfig field from os.environ in place.

    The conftest seeds these fields with ``pytest-*`` defaults; the
    encrypted-load tests must observe blob-supplied values, so the
    pre-set vars are popped before each scenario runs.
    """
    for line in FULL_ENV.splitlines():
        os.environ.pop(line.split("=", 1)[0], None)


def test_load_uses_license_key_file_only(tmp_path, monkeypatch):
    """Verify SensitiveConfig.load works with only the dropped key file.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    keyfile = tmp_path / ".license_key"
    keyfile.write_text(f"{LICENSE}\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)
    _clear_secret_env_vars()

    config = settings.SensitiveConfig.load()

    assert config.DOMAIN_NAME == "key-domain"
    assert config.API_KEY.get_secret_value() == "key-api-key"


def test_load_uses_license_key_env_only(tmp_path, monkeypatch):
    """Verify SensitiveConfig.load works with only the env-var key.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(
        settings, "LICENSE_KEY_PATH", tmp_path / "missing.license"
    )
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    _clear_secret_env_vars()

    config = settings.SensitiveConfig.load()

    assert config.MODEL_ID == "key-model"
    assert config.EMBED_API_KEY.get_secret_value() == "key-embed-api-key"


def test_load_with_env_var_and_file_uses_env(tmp_path, monkeypatch):
    """Verify the env-var key wins when both sources are present.

    The file holds a wrong key on purpose so decryption only succeeds
    when the env-var key is honoured; success therefore proves env
    precedence at the SensitiveConfig.load layer.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    keyfile = tmp_path / ".license_key"
    keyfile.write_text("wrong-key-on-disk\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    _clear_secret_env_vars()

    config = settings.SensitiveConfig.load()

    assert config.CODER_MODEL == "key-coder-model"
