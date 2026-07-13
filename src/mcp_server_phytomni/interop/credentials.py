# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Redacted parsing for operator-owned interop credential headers.

Classes: InteropCredentialError.
Functions: credential_headers, credential_references.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import SecretStr

_CREDENTIAL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORBIDDEN_CREDENTIAL_HEADERS = frozenset(
    {
        "accept-encoding",
        "connection",
        "content-length",
        "host",
        "transfer-encoding",
    }
)
_MAX_CREDENTIALS = 64
_MAX_HEADERS = 32
_MAX_HEADER_NAME_BYTES = 128
_MAX_HEADER_VALUE_BYTES = 8192


class InteropCredentialError(ValueError):
    """Raised when secret interop credential JSON has an invalid shape."""


def _load_credentials(raw: SecretStr) -> dict[str, dict[str, str]]:
    """Decode and validate credential headers without echoing input values."""
    try:
        payload = json.loads(raw.get_secret_value())
    except (json.JSONDecodeError, TypeError):
        raise InteropCredentialError(
            "INTEROP_CREDENTIALS must be valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise InteropCredentialError(
            "INTEROP_CREDENTIALS must be a JSON object"
        )
    if len(payload) > _MAX_CREDENTIALS:
        raise InteropCredentialError(
            "INTEROP_CREDENTIALS contains too many credentials"
        )

    validated: dict[str, dict[str, str]] = {}
    for reference, credential in payload.items():
        if not isinstance(
            reference, str
        ) or not _CREDENTIAL_REFERENCE.fullmatch(reference):
            raise InteropCredentialError(
                "INTEROP_CREDENTIALS contains an invalid credential reference"
            )
        validated[reference] = _validate_credential(credential)
    payload.clear()
    return validated


def _validate_credential(value: Any) -> dict[str, str]:
    """Validate one exact ``{"headers": {...}}`` credential object."""
    if not isinstance(value, dict) or set(value) != {"headers"}:
        raise InteropCredentialError(
            "each interop credential must contain only a headers mapping"
        )
    raw_headers = value["headers"]
    if not isinstance(raw_headers, dict) or not raw_headers:
        raise InteropCredentialError(
            "each interop credential headers value must be a non-empty mapping"
        )
    if len(raw_headers) > _MAX_HEADERS:
        raise InteropCredentialError(
            "an interop credential contains too many headers"
        )

    validated: dict[str, str] = {}
    normalized_names: set[str] = set()
    for name, header_value in raw_headers.items():
        if not _valid_header_name(name):
            raise InteropCredentialError(
                "an interop credential contains an invalid header"
            )
        normalized_name = name.lower()
        if (
            normalized_name in normalized_names
            or normalized_name in _FORBIDDEN_CREDENTIAL_HEADERS
        ):
            raise InteropCredentialError(
                "an interop credential contains an invalid header"
            )
        if not _valid_header_value(header_value):
            raise InteropCredentialError(
                "an interop credential contains an invalid header value"
            )
        normalized_names.add(normalized_name)
        validated[name] = header_value
    return validated


def _valid_header_name(value: Any) -> bool:
    """Return whether a value is one bounded ASCII HTTP field name."""
    return (
        isinstance(value, str)
        and len(value.encode("ascii", errors="ignore")) == len(value)
        and 0 < len(value) <= _MAX_HEADER_NAME_BYTES
        and _HEADER_NAME.fullmatch(value) is not None
    )


def _valid_header_value(value: Any) -> bool:
    """Return whether a value is bounded and free of header controls."""
    if not isinstance(value, str) or not value:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(encoded) > _MAX_HEADER_VALUE_BYTES:
        return False
    return value == value.strip(" ") and all(
        " " <= character <= "~" for character in value
    )


def credential_references(raw: SecretStr) -> frozenset[str]:
    """Validate the full secret JSON and return only credential references."""
    credentials = _load_credentials(raw)
    references = frozenset(credentials)
    credentials.clear()
    return references


def credential_headers(raw: SecretStr, reference: str) -> Mapping[str, str]:
    """Return a read-only copy of headers for one configured reference."""
    credentials = _load_credentials(raw)
    try:
        headers = dict(credentials[reference])
    except KeyError:
        raise InteropCredentialError(
            "interop credential reference is unavailable"
        ) from None
    finally:
        credentials.clear()
    return MappingProxyType(headers)


__all__ = [
    "InteropCredentialError",
    "credential_headers",
    "credential_references",
]
