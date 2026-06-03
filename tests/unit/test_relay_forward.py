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
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.forward import (
    RelayErrorMode,
    RelayFinishReason,
    RelayUpstream,
    TeeOutcome,
    build_relay_query,
    filter_response_headers,
    forward_relay_request,
    prepare_forward_headers,
    scrub_secrets,
    tee_and_stream,
    validate_relay_path_segment,
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


@pytest.mark.parametrize(
    "segment",
    ["", "..", "../etc", "a/b", "a b", "http://evil", "a%2fb", "a;b", "a?b"],
)
def test_validate_path_segment_rejects_injection(segment: str) -> None:
    """A traversal / separator / host char in a path segment is a 400."""
    with pytest.raises(HTTPException) as exc:
        validate_relay_path_segment(segment, field="task_id")

    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "segment", ["task-123", "Os01g0100100.v7", "abc_DEF", "9f8e7d6c"]
)
def test_validate_path_segment_accepts_safe_id(segment: str) -> None:
    """A safe identifier passes through unchanged."""
    assert validate_relay_path_segment(segment, field="task_id") == segment


def test_build_relay_query_keeps_only_allowlisted_keys() -> None:
    """Only allowlisted query keys survive; repeats and order are kept."""
    out = build_relay_query(
        "task_name=foo&evil=hack&task_name=bar", ("task_name",)
    )

    assert out == "task_name=foo&task_name=bar"


def test_build_relay_query_drops_everything_when_none_allowed() -> None:
    """A query with no allowlisted key yields an empty string."""
    assert build_relay_query("evil=1&x=2", ("task_name",)) == ""


def test_relay_upstream_trust_env_defaults_true() -> None:
    """RelayUpstream trusts the host proxy env by default (shared pool)."""

    async def _noinject() -> dict[str, str]:
        return {}

    upstream = RelayUpstream(
        url="https://x.test",
        error_mode=RelayErrorMode.ENVELOPE,
        service="s",
        inject_headers=_noinject,
    )

    assert upstream.trust_env is True


def _minimal_request(method: str = "GET") -> Request:
    """Build a header-free starlette Request for forward-core tests."""
    return Request(
        {
            "type": "http",
            "method": method,
            "headers": [],
            "query_string": b"",
            "path": "/",
        }
    )


def _recording_factory(recorded: dict[str, object]) -> object:
    """Return an async client factory that records its kwargs."""

    @contextlib.asynccontextmanager
    async def _factory(**kwargs: object) -> AsyncIterator[httpx.AsyncClient]:
        recorded["kwargs"] = kwargs
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=b"{}",
                )
            )
        ) as client:
            yield client

    return _factory


async def _forward_with_trust_env(
    monkeypatch: pytest.MonkeyPatch,
    audit_store: RelayAuditStore,
    *,
    trust_env: bool,
) -> dict[str, object]:
    """Run a buffered ENVELOPE forward, returning the client kwargs seen."""
    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        forward_module, "get_async_client", _recording_factory(recorded)
    )
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})

    async def _noinject() -> dict[str, str]:
        return {}

    upstream = RelayUpstream(
        url="https://x.test",
        error_mode=RelayErrorMode.ENVELOPE,
        service="spa_faq",
        inject_headers=_noinject,
        trust_env=trust_env,
    )
    response = await forward_relay_request(
        request=_minimal_request(),
        body=b"",
        upstream=upstream,
        principal=ApiPrincipal(
            user_id="u",
            key_prefix="ptm_x",
            scopes=frozenset({"relay:spa_faq"}),
        ),
        audit_store=audit_store,
    )
    assert response.status_code == 200
    return cast(dict[str, object], recorded["kwargs"])


async def test_forward_passes_trust_env_false_to_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """trust_env=False reaches get_async_client (ephemeral, proxy bypass)."""
    store = RelayAuditStore(str(tmp_path / "audit.sqlite"))
    kwargs = await _forward_with_trust_env(monkeypatch, store, trust_env=False)

    assert kwargs.get("trust_env") is False


async def test_forward_omits_trust_env_on_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A default upstream passes no trust_env, keeping the shared pool."""
    store = RelayAuditStore(str(tmp_path / "audit.sqlite"))
    kwargs = await _forward_with_trust_env(monkeypatch, store, trust_env=True)

    assert "trust_env" not in kwargs
