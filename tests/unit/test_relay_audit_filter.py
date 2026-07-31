# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay audit header/body filters.

Covers case-insensitive credential-header dropping, body redaction, caps,
and a binary placeholder.
"""

from __future__ import annotations

import json
import re

import pytest

from mcp_server_phytomni.api.relay.audit_filter import (
    CREDENTIAL_HEADERS,
    decode_body,
    drop_credential_headers,
    sanitize_audit_body,
)

pytestmark = pytest.mark.unit


def test_credential_header_allowlist_is_the_expected_set() -> None:
    """The dropped-header allowlist is the agreed credential set."""
    assert len(CREDENTIAL_HEADERS) == 7
    for header in re.findall(
        r"[a-z-]+",
        "authorization x-api-key x-service-token "
        "x-auth-token token cookie set-cookie",
    ):
        assert header in CREDENTIAL_HEADERS


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


def test_sanitize_audit_body_redacts_nested_json_credentials() -> None:
    """Nested credential-shaped JSON fields never reach the audit store."""
    raw = json.dumps(
        {
            "q": "héllo",
            "credentials": {
                "api_key": "ptm-secret",
                "nested": [{"access_token": "oauth-secret"}],
            },
        }
    ).encode()

    sanitized = json.loads(sanitize_audit_body(raw, max_bytes=4096))

    assert sanitized["q"] == "héllo"
    assert sanitized["credentials"]["api_key"] == "[REDACTED]"
    assert sanitized["credentials"]["nested"][0]["access_token"] == (
        "[REDACTED]"
    )
    assert "ptm-secret" not in json.dumps(sanitized)
    assert "oauth-secret" not in json.dumps(sanitized)


def test_sanitize_audit_body_redacts_form_encoded_credentials() -> None:
    """Non-JSON key/value bodies receive the same credential redaction."""
    raw = b"query=hello&api_key=ptm-secret&password=letmein"

    sanitized = sanitize_audit_body(raw, max_bytes=4096)

    assert "query=hello" in sanitized
    assert "api_key=[REDACTED]" in sanitized
    assert "password=[REDACTED]" in sanitized
    assert "ptm-secret" not in sanitized
    assert "letmein" not in sanitized


def test_sanitize_audit_body_caps_redacted_text() -> None:
    """Audit copies are capped after redaction, with an explicit marker."""
    raw = json.dumps({"prompt": "x" * 200, "token": "secret"}).encode()

    sanitized = sanitize_audit_body(raw, max_bytes=64)

    assert "secret" not in sanitized
    assert "<truncated:" in sanitized
    assert len(sanitized.encode()) > 64


def test_decode_body_returns_utf8_text_verbatim() -> None:
    """The low-level decoder preserves valid UTF-8 text."""
    raw = '{"q": "héllo", "n": 1}'.encode()

    assert decode_body(raw) == '{"q": "héllo", "n": 1}'


def test_decode_body_empty_is_empty_string() -> None:
    """An empty body decodes to an empty string."""
    assert decode_body(b"") == ""


def test_decode_body_binary_yields_placeholder() -> None:
    """Non-UTF-8 bytes are stored as a fixed placeholder, never raising."""
    assert decode_body(b"\xff\xfe\x00\x01") == "<binary body omitted>"
