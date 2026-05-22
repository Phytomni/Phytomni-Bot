# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests that SensitiveConfig.load picks up file-only license keys.

The previous load() implementation called ``os.getenv(LICENSE_KEY_ENV)``
directly and missed the Model A delivery path where the key arrives via
the on-disk ``LICENSE_KEY_PATH`` file instead of the environment. The
parametrized scenarios below pin end-to-end success for the file-only,
env-only, and both-set provisioning shapes through
``SensitiveConfig.load``.
"""

import os
from contextlib import contextmanager

import pytest

from mcp_server_phytomni.config import encrypt_env_file, settings

pytestmark = pytest.mark.unit

LICENSE = "customer-license-key-002"
WRONG_KEY = "wrong-key-on-disk"
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
_SENSITIVE_FIELDS = tuple(
    line.split("=", 1)[0] for line in FULL_ENV.splitlines()
)


@contextmanager
def _restored_environ():
    """Yield with os.environ restored on exit and sensitive fields stripped.

    The block's first move is dropping every SensitiveConfig-bound key
    from os.environ so the fixture sees the same blank slate the
    encrypted-load tests expect; the captured snapshot is rehydrated
    once the block exits so other tests inherit the conftest's seeded
    state untouched.

    Yields:
        None. Cleanup happens unconditionally via finally.
    """
    captured = dict(os.environ)
    try:
        for name in _SENSITIVE_FIELDS:
            os.environ.pop(name, None)
        yield
    finally:
        os.environ.clear()
        os.environ.update(captured)


@pytest.fixture(autouse=True)
def restored_environ(monkeypatch):
    """Reset decrypt memo and bracket os.environ around each test.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the memo.

    Yields:
        None. ``_restored_environ`` handles teardown.
    """
    monkeypatch.setattr(MEMO_PATH, {"done": False})
    with _restored_environ():
        yield


def _prepare_envelope(tmp_path, monkeypatch, *, env_value, file_content):
    """Wire an encrypted envelope plus the requested key sources.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
        env_value: Value to assign to PHYTOMNI_LICENSE_KEY (or None).
        file_content: Body to drop into LICENSE_KEY_PATH (or None).
    """
    plain_path = tmp_path.joinpath("plain.env")
    plain_path.write_text(FULL_ENV, encoding="utf-8")
    sealed_path = tmp_path.joinpath("plain.env.encrypted")
    encrypt_env_file(plain_path, LICENSE, sealed_path)
    keyfile_path = tmp_path.joinpath(".license_key")
    if file_content is not None:
        keyfile_path.write_text(file_content, encoding="utf-8")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", sealed_path)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile_path)
    monkeypatch.setattr(
        settings, "ENV_PATH", tmp_path.joinpath("absent-plaintext.env")
    )
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    if env_value is None:
        monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)
    else:
        monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", env_value)


@pytest.mark.parametrize(
    ("case_id", "env_value", "file_content"),
    [
        ("file_only", None, f"{LICENSE}\n"),
        ("env_only", LICENSE, None),
        ("env_wins_over_wrong_file", LICENSE, f"{WRONG_KEY}\n"),
    ],
)
def test_load_resolves_license_key(
    tmp_path, monkeypatch, case_id, env_value, file_content
):
    """Verify each provisioning shape decrypts and populates the config.

    The wrong-file-with-correct-env case proves env precedence at the
    SensitiveConfig.load layer because decryption only succeeds when
    the env-var key is honoured.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
        case_id: Human-readable scenario label for parametrize ids.
        env_value: Value passed to ``PHYTOMNI_LICENSE_KEY`` (or None).
        file_content: Body written to LICENSE_KEY_PATH (or None).
    """
    del case_id
    _prepare_envelope(
        tmp_path,
        monkeypatch,
        env_value=env_value,
        file_content=file_content,
    )

    config = settings.SensitiveConfig.load()

    assert config.DOMAIN_NAME == "key-domain"
    assert config.API_KEY.get_secret_value() == "key-api-key"
    assert config.EMBED_MODEL == "key-embed-model"
