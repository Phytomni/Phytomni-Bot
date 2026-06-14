# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the SensitiveConfig encrypted/plaintext load chain.

Covers the three load_env_file() paths (testing bypass, plaintext
.env first, encrypted envelope fallback), the once-per-process
decrypt memo, the env-wins setdefault rule, and the
plaintext-wins-over-encrypted regression.
"""

import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mcp_server_phytomni.config import (
    SecretEnvelopeError,
    encrypt_env_file,
    settings,
)
from mcp_server_phytomni.config.secret_envelope import (
    MAGIC,
    VERSION,
    derive_key,
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
    # Precondition: no plaintext .env so we reach the encrypted
    # fallback branch, not the plaintext-first branch.
    assert not settings.ENV_PATH.exists()

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

    with pytest.raises(SecretEnvelopeError):
        settings.load_env_file()


def _seal_raw_to_file(tmp_path, raw, license_key=LICENSE):
    """Seal raw bytes into an envelope file, bypassing UTF-8 validation.

    ``encrypt_env_file`` now rejects non-UTF-8 / BOM input, so a test
    that needs an already-sealed bad-encoding envelope on disk builds
    the blob directly from raw bytes with a deterministic salt and
    nonce (mirroring ``_seal_raw_bytes`` in ``test_secret_envelope``).

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        raw: Plaintext bytes to seal verbatim.
        license_key: License key used to derive the AES key.

    Returns:
        Path to the written .env.encrypted blob.
    """
    salt = b"\x00" * 16
    nonce = b"\x01" * 12
    key = derive_key(license_key, salt)
    sealed = AESGCM(key).encrypt(nonce, raw, None)
    blob = MAGIC + bytes([VERSION]) + salt + nonce + sealed
    path = tmp_path / ".env.encrypted"
    path.write_bytes(blob)
    return path


def test_load_env_file_rejects_sealed_non_utf8(tmp_path, monkeypatch):
    """Verify load_env_file surfaces a clear error on sealed GBK bytes.

    The customer incident was a startup-time load of a Windows-sealed
    non-UTF-8 ``.env.encrypted``. A correct-key decrypt must surface a
    ``SecretEnvelopeError`` naming the encoding rather than leaking a
    raw ``UnicodeDecodeError`` out of ``load_env_file``.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal_raw_to_file(tmp_path, "BASE_URL=请\n".encode("gbk"))
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

    with pytest.raises(SecretEnvelopeError, match="not UTF-8"):
        settings.load_env_file()


def test_load_env_file_rejects_sealed_bom(tmp_path, monkeypatch):
    """Verify load_env_file rejects a sealed BOM-prefixed envelope.

    A UTF-8 BOM passes ``bytes.decode('utf-8')`` but silently corrupts
    the first env key, so the startup loader must reject it explicitly
    with a clear ``SecretEnvelopeError`` rather than booting with a
    mangled first variable.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    blob = _seal_raw_to_file(tmp_path, b"\xef\xbb\xbfBASE_URL=x\n")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / "absent.env")
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

    with pytest.raises(SecretEnvelopeError, match="BOM"):
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


def test_plaintext_loads_env_when_sole_source(tmp_path, monkeypatch):
    """Verify a plaintext .env is loaded when no envelope is present.

    Plaintext is the primary (not fallback) source after the
    resolution-order inversion; this test's assertion is byte-
    identical to the legacy ``_fallback_`` variant, renamed because
    the "fallback" framing no longer matches the new precedence.

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()

    with pytest.raises(RuntimeError):
        settings.load_env_file()


def test_plaintext_wins_over_encrypted(tmp_path, monkeypatch):
    """Both .env and .env.encrypted + valid key → plaintext wins.

    Regression test for a dev-host scenario where a stale
    ``.env.encrypted`` left over from a customer-image dry run
    silently shadowed the developer's freshly edited ``.env``
    because the encrypted branch in ``load_env_file`` ran before
    the plaintext branch. After the priority inversion
    (plaintext-first), both the plaintext marker AND the absence
    of the encrypted marker must hold, and
    ``decrypt_env_blob`` must NOT be invoked.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    # Build an encrypted envelope holding a distinct marker key.
    blob = _seal(tmp_path, text=f"{FULL_ENV}{MARKER}=from-envelope\n")
    monkeypatch.setattr(settings, "ENCRYPTED_ENV_PATH", blob)

    # Build a plaintext .env holding a DIFFERENT marker that must win.
    plain = tmp_path / ".env"
    plain.write_text("PLAINTEXT_WINS=yes\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_PATH", plain)

    # A valid license key is available, so encrypted WOULD run if the
    # plaintext branch didn't short-circuit it.
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.setenv("PHYTOMNI_LICENSE_KEY", LICENSE)

    # Pre-populated markers must not be in the environment before load.
    os.environ.pop("PLAINTEXT_WINS", None)
    os.environ.pop(MARKER, None)

    # Replace decrypt_env_blob with a sentinel so that any invocation
    # of the encrypted path raises immediately — the assertion is that
    # this stub is NEVER called when plaintext .env is present.
    def _must_not_run(*_):
        raise AssertionError(
            "decrypt_env_blob must not run when plaintext .env exists"
        )

    monkeypatch.setattr(settings, "decrypt_env_blob", _must_not_run)
    monkeypatch.setattr(MEMO_PATH, {"done": False})

    assert settings.load_env_file() is True
    assert os.environ["PLAINTEXT_WINS"] == "yes"
    # Encrypted-only marker must be absent — decrypt did not run. The
    # sentinel above is strictly stronger than a memo-state check: if
    # ``decrypt_env_blob`` was never invoked, the memo could not have
    # been set to ``done=True`` either.
    assert MARKER not in os.environ


def test_get_sensitive_config_plaintext_path(tmp_path, monkeypatch):
    """Plaintext .env present → get_sensitive_config binds to ENV_PATH.

    After the priority inversion, ``ENV_PATH.exists()`` is the
    authoritative signal that plaintext was used; pydantic-settings
    re-reads the same file via its class-bound ``env_file=ENV_PATH``
    default, which is consistent because the file is identical to
    what ``load_dotenv`` already loaded into ``os.environ``.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture.
    """
    plain = tmp_path / ".env"
    plain.write_text(FULL_ENV, encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_PATH", plain)
    monkeypatch.setattr(
        settings, "ENCRYPTED_ENV_PATH", tmp_path / "absent.encrypted"
    )
    monkeypatch.delenv("PHYTOMNI_TESTING", raising=False)
    monkeypatch.delenv("PHYTOMNI_LICENSE_KEY", raising=False)
    for line in FULL_ENV.splitlines():
        os.environ.pop(line.split("=", 1)[0], None)
    settings.get_sensitive_config.cache_clear()

    config = settings.get_sensitive_config()

    assert config.DOMAIN_NAME == "enc-domain"
    assert config.API_KEY.get_secret_value() == "enc-api-key"
    assert config.EMBED_MODEL == "enc-embed-model"


def test_get_sensitive_config_encrypted_fallback(tmp_path, monkeypatch):
    """No plaintext .env, envelope + key present → bind _env_file=None.

    On the encrypted fallback path, decrypted values are already in
    ``os.environ`` via ``setdefault`` before
    ``get_sensitive_config`` instantiates ``SensitiveConfig``.
    Binding ``_env_file=None`` keeps the class-bound
    ``env_file=ENV_PATH`` default from trying to re-load a nonexistent
    plaintext file; the config is assembled purely from the injected
    environment variables.

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
    # Precondition: reach the encrypted fallback, not plaintext.
    assert not settings.ENV_PATH.exists()
    settings.get_sensitive_config.cache_clear()

    config = settings.get_sensitive_config()

    assert config.DOMAIN_NAME == "enc-domain"
    assert config.API_KEY.get_secret_value() == "enc-api-key"
    assert config.EMBED_MODEL == "enc-embed-model"
