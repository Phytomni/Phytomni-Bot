# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the AES-256-GCM .env secret envelope.

Covers the encrypt/decrypt round trip, dotenv parsing fidelity for
comments and blank lines, and every rejection path: wrong key,
tampered ciphertext, bad magic, and unsupported version.
"""

import pytest

from mcp_server_phytomni.config import (
    SecretEnvelopeError,
    decrypt_env_blob,
    encrypt_env_file,
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
