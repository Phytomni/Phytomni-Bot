# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the forward_relay_request orchestrator.

Drives the locked behaviors: read the upstream status before building
the response, pass a transparent status/body through while stripping the
caller credential and injecting the operator one, map a platform error
to the envelope, scrub an injected secret echoed in an error body, fail
closed on a mint failure, and keep a failed audit write from failing a
successful relay.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.forward import (
    RelayErrorMode,
    RelayUpstream,
    forward_relay_request,
)

pytestmark = pytest.mark.server

_Inject = Callable[[], Awaitable[dict[str, str]]]


def _make_request(headers: dict[str, str], *, method: str = "POST") -> Request:
    """Build a minimal Starlette request carrying the given headers."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": method,
        "path": "/v1/relay/llm/chat/completions",
        "headers": raw,
        "query_string": b"",
    }
    return Request(scope)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Point the forwarding core at a MockTransport-backed client."""

    @contextlib.asynccontextmanager
    async def _factory(
        **_kwargs: object,
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(forward_module, "get_async_client", _factory)


async def _openai_inject() -> dict[str, str]:
    """Return an OpenAI-style operator credential header."""
    return {"Authorization": "Bearer sk-operator-secret"}


def _upstream(
    error_mode: RelayErrorMode,
    *,
    url: str = "https://upstream.test/x",
    service: str = "llm",
    inject: _Inject = _openai_inject,
) -> RelayUpstream:
    """Build a RelayUpstream with test defaults."""
    return RelayUpstream(
        url=url,
        error_mode=error_mode,
        service=service,
        inject_headers=inject,
    )


async def _forward(
    store: RelayAuditStore,
    request: Request,
    upstream: RelayUpstream,
    *,
    body: bytes = b"",
) -> Response:
    """Invoke forward_relay_request with a test-default principal."""
    return await forward_relay_request(
        request=request,
        body=body,
        upstream=upstream,
        principal=ApiPrincipal(user_id="cust", key_prefix="ptm_abc"),
        audit_store=store,
    )


@pytest.fixture(name="store")
def _store_fixture(tmp_path: Path) -> RelayAuditStore:
    """Return a relay audit store backed by a throwaway database."""
    return RelayAuditStore(str(tmp_path / "relay_audit.sqlite"))


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


async def test_transparent_2xx_streams_with_upstream_status(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A 2xx upstream is streamed back with its status and audited."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok":true}',
        )

    _patch_client(monkeypatch, handler)

    response = await _forward(
        store,
        _make_request({"Authorization": "Bearer caller-key"}),
        _upstream(
            RelayErrorMode.TRANSPARENT,
            url="https://upstream.test/v1/chat/completions",
        ),
        body=b'{"q":1}',
    )
    assert isinstance(response, StreamingResponse)
    body = b"".join(
        [cast(bytes, chunk) async for chunk in response.body_iterator]
    )

    assert response.status_code == 200
    assert body == b'{"ok":true}'
    # Caller credential stripped, operator credential injected (T10/T11).
    assert seen[0].headers["authorization"] == "Bearer sk-operator-secret"
    records = store.query()
    assert len(records) == 1
    assert records[0].status_code == 200
    assert records[0].service == "llm"


async def test_transparent_upstream_500_seen_as_500(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A streamed-proxy upstream 500 reaches the client as 500, not 200."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b'{"error":"upstream boom"}')

    _patch_client(monkeypatch, handler)

    response = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )

    assert response.status_code == 500
    assert store.query()[0].status_code == 500


async def test_transparent_non2xx_scrubs_injected_secret(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """An injected secret echoed in a non-2xx body is masked to the client."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            content=b'{"error":"bad header Bearer sk-operator-secret"}',
        )

    _patch_client(monkeypatch, handler)

    response = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )

    assert response.status_code == 502
    assert b"sk-operator-secret" not in bytes(response.body)


async def test_injected_secret_never_in_client_headers(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A reflected credential header is dropped by the response allowlist."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-forwarded-authorization": "Bearer sk-operator-secret",
            },
            content=b"{}",
        )

    _patch_client(monkeypatch, handler)

    response = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )
    assert isinstance(response, StreamingResponse)
    async for _ in response.body_iterator:
        pass

    for value in response.headers.values():
        assert "sk-operator-secret" not in value


async def test_envelope_non2xx_raises_http_exception(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A platform-family upstream error maps to the unified envelope."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'{"detail":"down"}')

    _patch_client(monkeypatch, handler)

    with pytest.raises(HTTPException) as excinfo:
        await _forward(
            store,
            _make_request({}),
            _upstream(
                RelayErrorMode.ENVELOPE,
                url="https://upstream.test/retrieve",
                service="retrieve",
            ),
        )

    assert excinfo.value.status_code == 503
    assert store.query()[0].status_code == 503


async def test_envelope_2xx_returns_upstream_body(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A platform-family 2xx returns the upstream JSON body."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"docs":[]}',
        )

    _patch_client(monkeypatch, handler)

    response = await _forward(
        store,
        _make_request({}),
        _upstream(
            RelayErrorMode.ENVELOPE,
            url="https://upstream.test/retrieve",
            service="retrieve",
        ),
    )

    assert response.status_code == 200
    assert bytes(response.body) == b'{"docs":[]}'


async def test_inject_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A credential-mint failure never contacts upstream and is audited."""
    contacted = {"called": False}

    def handler(_req: httpx.Request) -> httpx.Response:
        contacted["called"] = True
        return httpx.Response(200, content=b"{}")

    _patch_client(monkeypatch, handler)

    async def _failing_inject() -> dict[str, str]:
        raise RuntimeError("IAM token unavailable")

    with pytest.raises(HTTPException) as excinfo:
        await _forward(
            store,
            _make_request({}),
            _upstream(
                RelayErrorMode.ENVELOPE,
                url="https://upstream.test/retrieve",
                service="retrieve",
                inject=_failing_inject,
            ),
        )

    assert excinfo.value.status_code == 502
    assert contacted["called"] is False
    record = store.query()[0]
    assert record.status_code is None
    assert record.error_type == "RuntimeError"


async def test_audit_write_failure_does_not_fail_call(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A failed audit write is logged but the relay still succeeds."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    _patch_client(monkeypatch, handler)

    def _boom(_record: object) -> int:
        raise OSError("audit disk full")

    monkeypatch.setattr(store, "record", _boom)

    response = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )
    assert isinstance(response, StreamingResponse)
    body = b"".join(
        [cast(bytes, chunk) async for chunk in response.body_iterator]
    )

    assert response.status_code == 200
    assert body == b"{}"


async def test_concurrency_cap_rejects_at_limit(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A second in-flight relay for one key is rejected with 503."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MAX_CONCURRENT_PER_KEY", "1")

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    _patch_client(monkeypatch, handler)

    held = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )
    assert isinstance(held, StreamingResponse)  # body unconsumed: slot held

    with pytest.raises(HTTPException) as excinfo:
        await _forward(
            store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
        )
    assert excinfo.value.status_code == 503

    async for _ in held.body_iterator:  # consume -> release the slot
        pass

    freed = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )
    assert isinstance(freed, StreamingResponse)
    async for _ in freed.body_iterator:
        pass


async def test_mint_failure_releases_concurrency_slot(
    monkeypatch: pytest.MonkeyPatch, store: RelayAuditStore
) -> None:
    """A failed relay frees its slot so the next call is not wedged."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MAX_CONCURRENT_PER_KEY", "1")

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    _patch_client(monkeypatch, handler)

    async def _failing_inject() -> dict[str, str]:
        raise RuntimeError("mint down")

    with pytest.raises(HTTPException):
        await _forward(
            store,
            _make_request({}),
            _upstream(RelayErrorMode.TRANSPARENT, inject=_failing_inject),
        )

    freed = await _forward(
        store, _make_request({}), _upstream(RelayErrorMode.TRANSPARENT)
    )
    assert isinstance(freed, StreamingResponse)
    async for _ in freed.body_iterator:
        pass
