# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the AES-256-GCM .env secret envelope.

Covers the encrypt/decrypt round trip, dotenv parsing fidelity for
comments and blank lines, and every rejection path: wrong key,
tampered ciphertext, bad magic, unsupported version, and non-UTF-8 /
BOM plaintext at both the encrypt and decrypt seams.
"""

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mcp_server_phytomni.config import (
    SecretEnvelopeError,
    decrypt_env_blob,
    encrypt_env_file,
)
from mcp_server_phytomni.config.secret_envelope import (
    MAGIC,
    VERSION,
    derive_key,
)

pytestmark = pytest.mark.unit

LICENSE = "customer-license-key-001"


def _seal(tmp_path, text, license_key=LICENSE):
    """Encrypt env text and return the envelope bytes.

    Args:
        tmp_path: Temporary directory for the plaintext and blob files.
        text: The plaintext .env contents to seal.
        license_key: License key used to derive the AES key.

    Returns:
        The raw envelope byte string.
    """
    src = tmp_path / ".env"
    src.write_text(text, encoding="utf-8")
    dest = tmp_path / ".env.encrypted"
    encrypt_env_file(src, license_key, dest)
    return dest.read_bytes()


def _seal_raw_bytes(raw, license_key=LICENSE):
    """Seal raw bytes into an envelope, bypassing UTF-8 validation.

    ``encrypt_env_file`` now rejects non-UTF-8 / BOM input, so a test
    that needs a *sealed* non-UTF-8 plaintext builds the envelope
    directly from the raw bytes with a deterministic salt and nonce.

    Args:
        raw: Plaintext bytes to seal verbatim.
        license_key: License key used to derive the AES key.

    Returns:
        The raw envelope byte string.
    """
    salt = b"\x00" * 16
    nonce = b"\x01" * 12
    key = derive_key(license_key, salt)
    sealed = AESGCM(key).encrypt(nonce, raw, None)
    return MAGIC + bytes([VERSION]) + salt + nonce + sealed


def test_round_trip_returns_original_mapping(tmp_path):
    """Verify a sealed .env decrypts back to the same mapping.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = _seal(
        tmp_path,
        "DOMAIN_NAME=acme\nUSER_PASSWORD=p@ss\nAPI_KEY=sk-123\n",
    )

    assert decrypt_env_blob(blob, LICENSE) == {
        "DOMAIN_NAME": "acme",
        "USER_PASSWORD": "p@ss",
        "API_KEY": "sk-123",
    }


def test_comments_and_blank_lines_are_dropped(tmp_path):
    """Verify dotenv parity: comments, blanks, quotes, export prefix.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = _seal(
        tmp_path,
        "# header comment\n"
        "\n"
        'API_KEY="sk with spaces"\n'
        "export BI_TOKEN=tok\n"
        "  \n"
        "EMBED_URL=https://e.example/v1  # trailing\n",
    )

    assert decrypt_env_blob(blob, LICENSE) == {
        "API_KEY": "sk with spaces",
        "BI_TOKEN": "tok",
        "EMBED_URL": "https://e.example/v1",
    }


def test_wrong_license_key_raises(tmp_path):
    """Verify decrypting with the wrong key is rejected.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = _seal(tmp_path, "API_KEY=sk-123\n")

    with pytest.raises(
        SecretEnvelopeError, match="wrong license key or corrupted file"
    ):
        decrypt_env_blob(blob, "not-the-right-key")


def test_tampered_ciphertext_raises(tmp_path):
    """Verify a single flipped ciphertext byte is rejected.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = bytearray(_seal(tmp_path, "API_KEY=sk-123\n"))
    blob[-1] ^= 0x01

    with pytest.raises(
        SecretEnvelopeError, match="wrong license key or corrupted file"
    ):
        decrypt_env_blob(bytes(blob), LICENSE)


def test_bad_magic_header_raises(tmp_path):
    """Verify a blob with the wrong magic prefix is rejected.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = _seal(tmp_path, "API_KEY=sk-123\n")

    with pytest.raises(SecretEnvelopeError, match="bad magic header"):
        decrypt_env_blob(b"NOPE0001" + blob[8:], LICENSE)


def test_unsupported_version_raises(tmp_path):
    """Verify a blob with an unknown version byte is rejected.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    blob = _seal(tmp_path, "API_KEY=sk-123\n")

    with pytest.raises(
        SecretEnvelopeError, match="unsupported envelope version: 9"
    ):
        decrypt_env_blob(blob[:8] + bytes([9]) + blob[9:], LICENSE)


def test_envelope_too_short_raises():
    """Verify a truncated blob shorter than the header is rejected."""
    with pytest.raises(SecretEnvelopeError, match="envelope too short"):
        decrypt_env_blob(b"PHYBOT01", LICENSE)


def test_encrypt_rejects_non_utf8_plaintext(tmp_path):
    """Verify encrypt_env_file rejects a non-UTF-8 (GBK) plaintext.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_bytes("BASE_URL=请\n".encode("gbk"))
    dest = tmp_path / ".env.encrypted"

    with pytest.raises(SecretEnvelopeError, match="not UTF-8"):
        encrypt_env_file(src, LICENSE, dest)

    assert not dest.exists()


def test_encrypt_rejects_bom_plaintext(tmp_path):
    """Verify encrypt_env_file rejects a UTF-8 BOM-prefixed plaintext.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_bytes(b"\xef\xbb\xbfBASE_URL=x\n")
    dest = tmp_path / ".env.encrypted"

    with pytest.raises(SecretEnvelopeError, match="BOM"):
        encrypt_env_file(src, LICENSE, dest)

    assert not dest.exists()


def test_decrypt_non_utf8_sealed_raises():
    """Verify a sealed non-UTF-8 blob fails with a clear UTF-8 error.

    A correct-key decrypt of a blob sealed from GBK bytes must surface
    a ``SecretEnvelopeError`` naming the encoding, not a raw
    ``UnicodeDecodeError`` leaking out of ``decrypt_env_blob``.
    """
    blob = _seal_raw_bytes("BASE_URL=请\n".encode("gbk"))

    with pytest.raises(SecretEnvelopeError, match="not UTF-8"):
        decrypt_env_blob(blob, LICENSE)


def test_decrypt_bom_sealed_raises():
    """Verify a sealed BOM-prefixed blob fails with a clear BOM error."""
    blob = _seal_raw_bytes(b"\xef\xbb\xbfBASE_URL=x\n")

    with pytest.raises(SecretEnvelopeError, match="BOM"):
        decrypt_env_blob(blob, LICENSE)
