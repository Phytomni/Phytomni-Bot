# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay audit header/body filters.

Covers case-insensitive credential-header dropping and verbatim body
decoding with a binary placeholder.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.relay.audit_filter import (
    CREDENTIAL_HEADERS,
    decode_body,
    drop_credential_headers,
)

pytestmark = pytest.mark.unit


def test_credential_header_allowlist_is_the_expected_set() -> None:
    """The dropped-header allowlist is the agreed credential set."""
    assert CREDENTIAL_HEADERS == frozenset(
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


def test_drop_credential_headers_removes_allowlist_case_insensitive() -> None:
    """Credential headers are dropped regardless of header casing."""
    headers = {
        "Authorization": "Bearer ptm_secret",
        "X-API-Key": "ptm_secret",
        "X-Auth-Token": "iam-token",
        "Token": "bi-token",
        "Cookie": "session=abc",
        "Set-Cookie": "session=abc",
        "Content-Type": "application/json",
        "X-Request-Id": "req-1",
    }

    filtered = drop_credential_headers(headers)

    assert filtered == {
        "Content-Type": "application/json",
        "X-Request-Id": "req-1",
    }


def test_drop_credential_headers_keeps_non_credentials_unchanged() -> None:
    """A header set with no credentials is returned as an equal copy."""
    headers = {"Content-Type": "application/json", "Accept": "*/*"}

    filtered = drop_credential_headers(headers)

    assert filtered == headers
    assert filtered is not headers


def test_decode_body_returns_utf8_text_verbatim() -> None:
    """A UTF-8 body is decoded without redaction or truncation."""
    raw = '{"q": "héllo", "n": 1}'.encode("utf-8")

    assert decode_body(raw) == '{"q": "héllo", "n": 1}'


def test_decode_body_empty_is_empty_string() -> None:
    """An empty body decodes to an empty string."""
    assert decode_body(b"") == ""


def test_decode_body_binary_yields_placeholder() -> None:
    """Non-UTF-8 bytes are stored as a fixed placeholder, never raising."""
    assert decode_body(b"\xff\xfe\x00\x01") == "<binary body omitted>"
