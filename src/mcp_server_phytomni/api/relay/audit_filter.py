# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Header and body filters for the relay audit store.

Functions: drop_credential_headers, decode_body.

Bodies are persisted verbatim, with no redaction or truncation (D-D).
The only enforced floor (AR-2) is that the operator's injected upstream
credentials, which travel in request headers, are dropped before any
header set is recorded or logged, so they never reach the audit DB.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "CREDENTIAL_HEADERS",
    "drop_credential_headers",
    "decode_body",
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
    """Decode a raw body for verbatim audit storage.

    Bodies are stored without redaction or truncation (D-D). Valid UTF-8
    is returned as-is; bytes that are not valid UTF-8 yield a fixed
    placeholder rather than raising or storing mojibake.

    Args:
        raw: The raw request or response body bytes.

    Returns:
        The decoded body, or ``"<binary body omitted>"`` for binary data.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return _BINARY_BODY_PLACEHOLDER
