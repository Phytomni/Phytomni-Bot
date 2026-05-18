# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the SensitiveConfig encrypted/plaintext load chain.

Covers the four resolution paths of load_env_file (testing bypass,
encrypted envelope, plaintext fallback, no-source RuntimeError) plus
the once-per-process decrypt memo, the env-wins setdefault rule,
wrong-key propagation, and an end-to-end encrypted load().
"""

import os

import pytest

from mcp_server_phytomni.config import (
    SecretEnvelopeError,
    encrypt_env_file,
    settings,
)

pytestmark = pytest.mark.unit

LICENSE = "customer-license-key-001"
MEMO_PATH = "mcp_server_phytomni.config.settings._ENV_DECRYPT_MEMO"

# Every field SensitiveConfig requires (BI_TOKEN has a default).
FULL_ENV = (
    "DOMAIN_NAME=enc-domain\n"
    "USER_NAME=enc-user\n"
    "USER_PASSWORD=enc-password\n"
    "ACCESS_KEY_ID=enc-access-key\n"
    "SECRET_ACCESS_KEY=enc-secret-key\n"
    "BASE_URL=https://enc.example/llm\n"
    "MODEL_ID=enc-model\n"
    "API_KEY=enc-api-key\n"
    "CODER_URL=https://enc.example/coder\n"
    "CODER_MODEL=enc-coder-model\n"
    "CODER_API_KEY=enc-coder-api-key\n"
    "EMBED_URL=https://enc.example/embed\n"
    "EMBED_MODEL=enc-embed-model\n"
    "EMBED_API_KEY=enc-embed-api-key\n"
)
# A key absent from conftest's _TEST_ENV, so its presence/absence in
# os.environ is an unambiguous signal that decryption did/didn't run.
MARKER = "ENC_ONLY_MARKER"
MARKED_ENV = FULL_ENV + f"{MARKER}=marker-value\n"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Snapshot os.environ and reset the decrypt memo per test.

    load_env_file injects keys via os.environ.setdefault, which
    monkeypatch does not undo; a full snapshot keeps the session
    environment conftest installed intact for later tests. The memo
    is reset through its string path so the production module global
    is rebound (and auto-restored) without a protected `.` access.

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


def _seal(tmp_path, text=MARKED_ENV, license_key=LICENSE):
    """Write a plaintext .env and return its sealed envelope path.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        text: Plaintext .env body to seal.
        license_key: License key used to derive the AES key.

    Returns:
        Path to the written .env.encrypted blob.
    """
    src = tmp_path / ".env"
    src.write_text(text, encoding="utf-8")
    blob = tmp_path / ".env.encrypted"
    encrypt_env_file(src, license_key, blob)
    return blob


def test_testing_bypass_skips_decryption(tmp_path, monkeypatch):
    """Verify PHYTOMNI_TESTING=1 returns early without decrypting.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setenv("PHYTOMNI_TESTING", "1")
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    os.environ.pop(MARKER, None)

    assert settings.load_env_file() is True
    assert MARKER not in os.environ


def test_encrypted_load_injects_absent_keys(tmp_path, monkeypatch):
    """Verify an absent key is filled from the decrypted envelope.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    os.environ.pop(MARKER, None)
    os.environ.pop("DOMAIN_NAME", None)

    assert settings.load_env_file() is True
    assert os.environ[MARKER] == "marker-value"
    assert os.environ["DOMAIN_NAME"] == "enc-domain"


def test_environment_wins_over_envelope(tmp_path, monkeypatch):
    """Verify a pre-set env value is not overwritten by the blob.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    monkeypatch.setenv("API_KEY", "operator-override")

    assert settings.load_env_file() is True
    assert os.environ["API_KEY"] == "operator-override"


def test_memo_decrypts_once_per_process(tmp_path, monkeypatch):
    """Verify the memo runs the PBKDF2 decrypt at most once.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    real = settings.decrypt_env_blob
    calls: list[int] = []

    def counting(blob_bytes, key):
        calls.append(1)
        return real(blob_bytes, key)

    monkeypatch.setattr(settings, "decrypt_env_blob", counting)

    settings.load_env_file()
    settings.load_env_file()

    assert len(calls) == 1


def test_wrong_license_key_propagates(tmp_path, monkeypatch):
    """Verify a wrong license key raises instead of empty secrets.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", "wrong-key")

    with pytest.raises(SecretEnvelopeError):
        settings.load_env_file()


def test_no_source_raises_runtime_error(tmp_path, monkeypatch):
    """Verify the no-source path raises RuntimeError listing options.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        settings, "ENCRYPTED_ENV_PATH", tmp_path / "absent.encrypted"
    )
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        settings.load_env_file()
    message = str(excinfo.value)
    assert "PHYTOMNI_TESTING" in message
    assert "PHYTOMNI_LICENSE_KEY" in message
    assert ".env.example" in message


def test_plaintext_fallback_loads_env(tmp_path, monkeypatch):
    """Verify a plaintext .env is still loaded when no envelope.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    plain = tmp_path / ".env"
    plain.write_text("PLAINTEXT_MARKER=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_PATH", plain)
    monkeypatch.setattr(
        settings, "ENCRYPTED_ENV_PATH", tmp_path / "absent.encrypted"
    )
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)

    assert settings.load_env_file() is True
    assert os.environ["PLAINTEXT_MARKER"] == "from-dotenv"


def test_load_end_to_end_encrypted(tmp_path, monkeypatch):
    """Verify SensitiveConfig.load() reads decrypted values fully.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path, text=FULL_ENV)
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    for line in FULL_ENV.splitlines():
        os.environ.pop(line.split("=", 1)[0], None)

    config = settings.SensitiveConfig.load()

    assert config.DOMAIN_NAME == "enc-domain"
    assert config.API_KEY.get_secret_value() == "enc-api-key"
    assert config.EMBED_MODEL == "enc-embed-model"


def _write_keyfile(tmp_path, text):
    """Write a Model-A license-key file and return its path.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        text: Raw file body (callers include trailing newlines on
            purpose to exercise the whitespace-stripping rule).

    Returns:
        Path to the written .license_key file.
    """
    keyfile = tmp_path / ".license_key"
    keyfile.write_text(text, encoding="utf-8")
    return keyfile


def test_license_key_file_drives_decryption(tmp_path, monkeypatch):
    """Verify the LICENSE_KEY_PATH file alone unlocks the envelope.

    No PHYTOMNI_LICENSE_KEY env var is set; the key arrives only via
    the dropped file (with a trailing newline, as `echo` would write).

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    keyfile = _write_keyfile(tmp_path, f"{LICENSE}\n")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)
    os.environ.pop(MARKER, None)

    assert settings.load_env_file() is True
    assert os.environ[MARKER] == "marker-value"


def test_env_var_wins_over_license_key_file(tmp_path, monkeypatch):
    """Verify the env var beats a (wrong) on-disk key file.

    The file holds a wrong key; only the correct env var lets the
    decrypt succeed, so success proves env precedence unambiguously.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    keyfile = _write_keyfile(tmp_path, "wrong-key-in-file\n")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    os.environ.pop(MARKER, None)

    assert settings.load_env_file() is True
    assert os.environ[MARKER] == "marker-value"


def test_empty_license_key_file_is_treated_as_absent(tmp_path, monkeypatch):
    """Verify a blank key file falls through, not a failed decrypt.

    With no env var, an empty/whitespace-only file present, an
    envelope present, and no plaintext .env, resolution must reach
    the RuntimeError (key treated as absent) rather than attempting a
    guaranteed-failing PBKDF2 decrypt.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal(tmp_path)
    keyfile = _write_keyfile(tmp_path, "   \n")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "LICENSE_KEY_PATH", keyfile)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)

    with pytest.raises(RuntimeError):
        settings.load_env_file()
