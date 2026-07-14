# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Header/body filters for relay audit records.

Drop credential headers and redact/cap untrusted audit bodies.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

__all__ = [
    "CREDENTIAL_HEADERS",
    "drop_credential_headers",
    "decode_body",
    "redact_body_text",
    "sanitize_audit_body",
]

# Lowercase header names that may carry a credential. Both the inbound
# relay key and the operator's injected upstream secret live in headers,
# so these are dropped before any header set is persisted or logged.
CREDENTIAL_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-service-token",
        "x-auth-token",
        "token",
        "cookie",
        "set-cookie",
    }
)

_BINARY_BODY_PLACEHOLDER = "<binary body omitted>"
_REDACTED = "[REDACTED]"
_TRUNCATED = "<truncated: {bytes} bytes>"

_SENSITIVE_BODY_KEYS: frozenset[str] = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "authorization",
        "authtoken",
        "bitoken",
        "clientsecret",
        "cookie",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "secretaccesskey",
        "servicetoken",
        "setcookie",
        "token",
        "xapikey",
        "xauthtoken",
        "xservicetoken",
    }
)

_KEY_VALUE_SECRET = re.compile(
    r"(?i)(?P<prefix>\b(?:authorization|api[-_]?key|x[-_]?api[-_]?key|"
    r"service[-_]?token|x[-_]?service[-_]?token|auth[-_]?token|"
    r"access[-_]?token|refresh[-_]?token|id[-_]?token|client[-_]?secret|"
    r"secret|password|passwd|cookie|set[-_]?cookie)\b\s*[:=]\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^'\"\s,;&}]+)(?P=quote)"
)
_AUTHORIZATION_SECRET = re.compile(
    r"(?i)(\bauthorization\b\s*[:=]\s*(?:bearer|basic)\s+)" + r"[^\s,;&}]+"
)


def _normalize_body_key(key: str) -> str:
    """Normalize a JSON key for credential-name matching."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_sensitive_body_key(key: object) -> bool:
    """Return whether a JSON key is treated as credential-bearing."""
    return (
        isinstance(key, str)
        and _normalize_body_key(key) in _SENSITIVE_BODY_KEYS
    )


def _redact_json(value: object) -> object:
    """Recursively replace credential-shaped JSON values."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                _REDACTED
                if _is_sensitive_body_key(key)
                else _redact_json(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def _redact_text(text: str) -> str:
    """Redact a JSON or key/value text body without changing its encoding."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        redacted = _AUTHORIZATION_SECRET.sub(r"\1" + _REDACTED, text)
        return _KEY_VALUE_SECRET.sub(r"\g<prefix>" + _REDACTED, redacted)
    redacted_json = _redact_json(parsed)
    if redacted_json == parsed:
        return text
    return json.dumps(redacted_json, ensure_ascii=False, separators=(",", ":"))


def redact_body_text(
    text: str | None, max_bytes: int | None = None
) -> str | None:
    """Redact credential-shaped fields and optionally cap an audit text body.

    Args:
        text: Decoded body text, or ``None`` for an absent body.
        max_bytes: Optional UTF-8 byte cap for the returned audit copy.

    Returns:
        Redacted text, with a truncation marker when the cap is exceeded.
    """
    if text is None:
        return None
    redacted = _redact_text(text)
    if max_bytes is None or len(redacted.encode("utf-8")) <= max_bytes:
        return redacted
    encoded = redacted.encode("utf-8")
    clipped = encoded[: max(0, max_bytes)].decode("utf-8", errors="ignore")
    return clipped + _TRUNCATED.format(
        bytes=len(encoded) - len(clipped.encode())
    )


def drop_credential_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with credential-bearing keys removed.

    Matching is case-insensitive against ``CREDENTIAL_HEADERS``.

    Args:
        headers: The header set to filter.

    Returns:
        A new dict preserving every non-credential header unchanged.
    """
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in CREDENTIAL_HEADERS
    }


def decode_body(raw: bytes) -> str:
    """Decode a raw body before audit redaction.

    Valid UTF-8 is returned as-is; bytes that are not valid UTF-8 yield a
    fixed placeholder rather than raising or storing mojibake.

    Args:
        raw: The raw request or response body bytes.

    Returns:
        The decoded body, or ``"<binary body omitted>"`` for binary data.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return _BINARY_BODY_PLACEHOLDER


def sanitize_audit_body(raw: bytes, max_bytes: int) -> str:
    """Decode, redact, and cap one request or response audit body.

    Args:
        raw: Raw body bytes received from a caller or upstream.
        max_bytes: Maximum UTF-8 bytes retained before the marker is added.

    Returns:
        A safe audit copy. Binary bodies use a fixed placeholder.
    """
    decoded = decode_body(raw)
    if decoded == _BINARY_BODY_PLACEHOLDER:
        return decoded
    return redact_body_text(decoded, max_bytes) or ""
