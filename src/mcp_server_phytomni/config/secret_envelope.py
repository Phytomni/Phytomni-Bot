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
_UTF8_BOM = b"\xef\xbb\xbf"


class SecretEnvelopeError(Exception):
    """Raised when an encrypted .env blob cannot be opened.

    The message is deliberately ambiguous about whether the cause is a
    wrong license key or a corrupted file. AES-GCM authentication makes
    the two cryptographically indistinguishable, and not separating
    them avoids handing an attacker a decryption oracle.

    That deliberate ambiguity applies only to the authentication-failure
    message. A post-decryption failure (non-UTF-8 or BOM-prefixed sealed
    plaintext) may carry a specific message because reaching it already
    required a valid key, so it leaks no decryption oracle.
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


def _decode_sealed_utf8(raw: bytes, *, context: str) -> str:
    """Decode envelope plaintext as strict UTF-8, rejecting a BOM.

    Both the encrypt seam (validating the source ``.env`` before
    sealing) and the decrypt seam (decoding the sealed bytes) route
    through here, so the two enforce one invariant with one message
    vocabulary: the plaintext must be valid UTF-8 with no byte-order
    mark. A leading BOM passes ``bytes.decode('utf-8')`` but silently
    corrupts the first dotenv key, so it is rejected explicitly rather
    than decoded.

    Args:
        raw: Candidate plaintext bytes.
        context: Human-readable subject for the error message (the
            source path on encrypt, ``"sealed .env"`` on decrypt).

    Returns:
        The decoded UTF-8 text.

    Raises:
        SecretEnvelopeError: If ``raw`` starts with a UTF-8 BOM or is
            not valid UTF-8.
    """
    if raw.startswith(_UTF8_BOM):
        raise SecretEnvelopeError(
            f"{context} has a UTF-8 BOM; re-save as UTF-8 without a "
            "BOM (Windows editors often add one)."
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretEnvelopeError(
            f"{context} is not UTF-8 ({exc}); re-save as UTF-8 without "
            "a BOM (Windows editors often default to GBK/ANSI)."
        ) from exc


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
    _decode_sealed_utf8(plaintext, context=str(plaintext_path))
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
    text = _decode_sealed_utf8(plaintext, context="sealed .env")
    parsed = dotenv_values(stream=io.StringIO(text))
    return {k: v for k, v in parsed.items() if v is not None}
