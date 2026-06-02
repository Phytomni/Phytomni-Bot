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

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from mcp_server_phytomni.api.relay.forward import (
    RelayFinishReason,
    TeeOutcome,
    filter_response_headers,
    prepare_forward_headers,
    scrub_secrets,
    tee_and_stream,
)

pytestmark = pytest.mark.unit


async def _source(
    chunks: list[bytes], *, raise_after: bool = False
) -> AsyncIterator[bytes]:
    """Yield chunks, optionally raising an upstream read error at the end."""
    for chunk in chunks:
        yield chunk
    if raise_after:
        raise httpx.ReadError("upstream dropped")


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


async def test_tee_streams_all_and_audits_full_under_cap() -> None:
    """Under the cap the client and the audit copy both get everything."""
    captured: list[TeeOutcome] = []

    async def _capture(outcome: TeeOutcome) -> None:
        captured.append(outcome)

    streamed = [
        chunk
        async for chunk in tee_and_stream(
            _source([b"ab", b"cd"]), audit_cap=100, on_complete=_capture
        )
    ]

    assert b"".join(streamed) == b"abcd"
    assert captured[0].body == b"abcd"
    assert captured[0].finish_reason is RelayFinishReason.COMPLETE
    assert captured[0].truncated is False
    assert captured[0].total_bytes == 4


async def test_tee_caps_audit_copy_but_streams_full_to_client() -> None:
    """The audit copy stops at the cap while the client gets every byte."""
    captured: list[TeeOutcome] = []

    async def _capture(outcome: TeeOutcome) -> None:
        captured.append(outcome)

    streamed = [
        chunk
        async for chunk in tee_and_stream(
            _source([b"aaaa", b"bbbb"]), audit_cap=5, on_complete=_capture
        )
    ]

    assert b"".join(streamed) == b"aaaabbbb"
    assert captured[0].body == b"aaaab"
    assert captured[0].truncated is True
    assert captured[0].total_bytes == 8


async def test_tee_marks_upstream_abort_without_propagating() -> None:
    """An upstream mid-stream error is audited, not raised to the client."""
    captured: list[TeeOutcome] = []

    async def _capture(outcome: TeeOutcome) -> None:
        captured.append(outcome)

    streamed = [
        chunk
        async for chunk in tee_and_stream(
            _source([b"ab"], raise_after=True),
            audit_cap=100,
            on_complete=_capture,
        )
    ]

    assert b"".join(streamed) == b"ab"
    assert captured[0].finish_reason is RelayFinishReason.UPSTREAM_ABORTED
    assert captured[0].body == b"ab"
    assert captured[0].error_type == "ReadError"


async def test_tee_deadline_aborts_slow_upstream() -> None:
    """A drip-feeding upstream is torn down at the wall-clock deadline."""
    captured: list[TeeOutcome] = []

    async def _capture(outcome: TeeOutcome) -> None:
        captured.append(outcome)

    async def _slow() -> AsyncIterator[bytes]:
        yield b"a"
        await asyncio.sleep(0.2)
        yield b"b"

    streamed = [
        chunk
        async for chunk in tee_and_stream(
            _slow(), audit_cap=100, on_complete=_capture, deadline=0.05
        )
    ]

    assert b"".join(streamed) == b"a"
    assert captured[0].finish_reason is RelayFinishReason.DEADLINE_EXCEEDED


async def test_tee_marks_client_disconnect_on_aclose() -> None:
    """Closing the stream early records a client disconnect, not an abort."""
    captured: list[TeeOutcome] = []

    async def _capture(outcome: TeeOutcome) -> None:
        captured.append(outcome)

    gen = tee_and_stream(
        _source([b"ab", b"cd"]), audit_cap=100, on_complete=_capture
    )
    first = await gen.__anext__()
    await gen.aclose()

    assert first == b"ab"
    assert captured[0].finish_reason is RelayFinishReason.CLIENT_DISCONNECTED
    assert captured[0].body == b"ab"
