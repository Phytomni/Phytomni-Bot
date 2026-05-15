# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""AES-256-GCM envelope for shipping an encrypted .env in images.

Classes: SecretEnvelopeError.
Functions: derive_key, encrypt_env_file, decrypt_env_blob.
"""

import io
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import dotenv_values

MAGIC = b"PHYBOT01"
VERSION = 1
PBKDF2_ITERATIONS = 600_000

_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
_VERSION_LEN = 1
_HEADER_LEN = len(MAGIC) + _VERSION_LEN + _SALT_LEN + _NONCE_LEN


class SecretEnvelopeError(Exception):
    """Raised when an encrypted .env blob cannot be opened.

    The message is deliberately ambiguous about whether the cause is a
    wrong license key or a corrupted file. AES-GCM authentication makes
    the two cryptographically indistinguishable, and not separating
    them avoids handing an attacker a decryption oracle.
    """


def derive_key(license_key: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from a license key and salt.

    Args:
        license_key: The per-customer license string.
        salt: Random salt stored in the envelope header.

    Returns:
        A 32-byte key suitable for AES-256-GCM.
    """
    kdf = PBKDF2HMAC(
        algorithm=SHA256(),
        length=_KEY_LEN,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(license_key.encode("utf-8"))


def encrypt_env_file(
    plaintext_path: Path, license_key: str, dest: Path
) -> None:
    """Encrypt a plaintext .env file into an envelope blob.

    Plaintext bytes are sealed verbatim; no .env parsing happens on the
    encrypt side so the envelope stays a dumb byte container.

    Args:
        plaintext_path: Path to the readable plaintext .env file.
        license_key: The per-customer license string.
        dest: Path the envelope blob is written to.
    """
    plaintext = plaintext_path.read_bytes()
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = derive_key(license_key, salt)
    sealed = AESGCM(key).encrypt(nonce, plaintext, None)
    blob = MAGIC + bytes([VERSION]) + salt + nonce + sealed
    dest.write_bytes(blob)


def decrypt_env_blob(blob: bytes, license_key: str) -> dict[str, str]:
    """Decrypt an envelope blob into environment key/value pairs.

    Parsing of the decrypted text is delegated to python-dotenv so the
    result matches what ``load_dotenv`` produces for the same plaintext
    .env (quoting, ``export`` prefixes, escaping).

    Args:
        blob: The full envelope byte string.
        license_key: The per-customer license string.

    Returns:
        Mapping of environment variable names to their values.

    Raises:
        SecretEnvelopeError: If the header is malformed, the version is
            unsupported, or authentication fails (wrong key or
            corrupted file).
    """
    if len(blob) < _HEADER_LEN:
        raise SecretEnvelopeError("envelope too short")
    if blob[: len(MAGIC)] != MAGIC:
        raise SecretEnvelopeError("bad magic header")
    version = blob[len(MAGIC)]
    if version != VERSION:
        raise SecretEnvelopeError(f"unsupported envelope version: {version}")
    off = len(MAGIC) + _VERSION_LEN
    salt = blob[off : off + _SALT_LEN]
    off += _SALT_LEN
    nonce = blob[off : off + _NONCE_LEN]
    off += _NONCE_LEN
    sealed = blob[off:]
    key = derive_key(license_key, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, None)
    except InvalidTag as exc:
        raise SecretEnvelopeError(
            "wrong license key or corrupted file"
        ) from exc
    parsed = dotenv_values(stream=io.StringIO(plaintext.decode("utf-8")))
    return {k: v for k, v in parsed.items() if v is not None}
