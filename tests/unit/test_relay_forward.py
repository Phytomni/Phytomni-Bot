# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay forwarding-core header and body helpers.

Covers the request-side strip set (credentials, Host, Content-Length,
hop-by-hop), the response-side allowlist (only Content-Type survives so
no reflected operator credential leaks), and the non-2xx body secret
scrub.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.relay.forward import (
    filter_response_headers,
    prepare_forward_headers,
    scrub_secrets,
)

pytestmark = pytest.mark.unit


def test_prepare_forward_headers_strips_credentials() -> None:
    """Inbound caller credentials never reach the upstream request."""
    out = prepare_forward_headers(
        {
            "Authorization": "Bearer caller-key",
            "X-Api-Key": "caller",
            "Cookie": "session=abc",
            "Content-Type": "application/json",
        }
    )

    assert "authorization" not in {k.lower() for k in out}
    assert "x-api-key" not in {k.lower() for k in out}
    assert "cookie" not in {k.lower() for k in out}
    assert out["Content-Type"] == "application/json"


def test_prepare_forward_headers_strips_host_and_content_length() -> None:
    """Host and Content-Length are dropped so httpx recomputes them."""
    out = prepare_forward_headers(
        {
            "Host": "relay.example",
            "Content-Length": "999999",
            "X-Keep": "v",
        }
    )

    lowered = {k.lower() for k in out}
    assert "host" not in lowered
    assert "content-length" not in lowered
    assert out["X-Keep"] == "v"


def test_prepare_forward_headers_strips_hop_by_hop() -> None:
    """Hop-by-hop headers are not forwarded to the upstream."""
    out = prepare_forward_headers(
        {
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "Upgrade": "h2c",
            "X-Keep": "v",
        }
    )

    lowered = {k.lower() for k in out}
    assert lowered == {"x-keep"}


def test_filter_response_headers_is_an_allowlist() -> None:
    """Only Content-Type survives; reflected/secret headers are dropped."""
    out = filter_response_headers(
        {
            "Content-Type": "text/event-stream",
            "Set-Cookie": "x=y",
            "WWW-Authenticate": 'Bearer realm="op.internal"',
            "X-Forwarded-Authorization": "Bearer operator-secret",
            "X-Vendor-Trace": "abc",
        }
    )

    assert out == {"Content-Type": "text/event-stream"}


def test_scrub_secrets_masks_injected_values() -> None:
    """A leaked injected secret in an error body is masked out."""
    body = b'{"error":"bad token Bearer sk-operator-123"}'

    out = scrub_secrets(body, ["Bearer sk-operator-123"])

    assert b"sk-operator-123" not in out
    assert b"***redacted***" in out


def test_scrub_secrets_empty_secret_is_noop() -> None:
    """An empty secret never blanket-masks the whole body."""
    body = b"untouched"

    assert scrub_secrets(body, [""]) == b"untouched"


def test_scrub_secrets_absent_secret_returns_body_unchanged() -> None:
    """A body that does not contain the secret is returned verbatim."""
    body = b'{"ok":true}'

    assert scrub_secrets(body, ["Bearer sk-operator-123"]) == body
