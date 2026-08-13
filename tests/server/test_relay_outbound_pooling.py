# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Relay final-service mapping and streamed lease-lifetime tests."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.responses import StreamingResponse
from pydantic import SecretStr
from starlette.requests import ClientDisconnect, Request
from tests.support.outbound_fakes import ControlledByteStream
from tests.support.relay_request import relay_request_scope

from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.forward import (
    RelayErrorMode,
    RelayUpstream,
    forward_relay_request,
)
from mcp_server_phytomni.common.relay_client import (
    RelayClient,
    RelayRequestOptions,
)
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    aclose_outbound_runtime,
)

pytestmark = pytest.mark.server

_TEST_TIMEOUT_SECONDS = 2.0


def _request() -> Request:
    """Build a minimal relay request for the forwarding seam."""
    return Request(relay_request_scope())


async def _no_inject() -> dict[str, str]:
    """Use no credential when the forwarding lifetime is under test."""
    return {}


def _upstream() -> RelayUpstream:
    """Return a transparent typed upstream for the stream tests."""
    return RelayUpstream(
        url="https://upstream.test/v1/chat/completions",
        error_mode=RelayErrorMode.TRANSPARENT,
        service="llm",
        inject_headers=_no_inject,
        pool=OutboundPoolName.LLM,
    )


async def _body(response: StreamingResponse) -> bytes:
    """Consume a Starlette response iterator without a server task."""
    iterator = cast(Any, response.body_iterator)
    return b"".join([chunk async for chunk in iterator])


async def _wait_for_event(event: asyncio.Event) -> None:
    """Wait for a test synchronization event under a hard deadline."""
    await asyncio.wait_for(event.wait(), timeout=_TEST_TIMEOUT_SECONDS)


async def _settle_task(task: asyncio.Task[Any]) -> None:
    """Cancel and reap a spawned test task under a hard deadline."""
    if not task.done():
        task.cancel()
    await asyncio.wait_for(
        asyncio.gather(task, return_exceptions=True),
        timeout=_TEST_TIMEOUT_SECONDS,
    )


def _assert_terminal_outcome(
    snapshot: Any,
    *,
    completed: int = 0,
    failed: int = 0,
    cancelled: int = 0,
) -> None:
    """Assert one released lease has exactly one classified outcome."""
    assert snapshot.started == 1
    assert snapshot.completed == completed
    assert snapshot.failed == failed
    assert snapshot.cancelled == cancelled
    assert snapshot.started == (
        snapshot.completed + snapshot.failed + snapshot.cancelled
    )
    assert snapshot.in_use == 0
    assert snapshot.waiting == 0


def _assert_source_closed_before_release(close_snapshots: list[Any]) -> None:
    """Assert the source closed once while its target lease was held."""
    assert len(close_snapshots) == 1
    assert close_snapshots[0].in_use == 1


def _record_response_closes(
    monkeypatch: pytest.MonkeyPatch,
    response: Any,
) -> list[None]:
    """Count one upstream response's close calls without changing behavior."""
    close_calls: list[None] = []
    original_close = response.aclose

    async def close() -> None:
        close_calls.append(None)
        await original_close()

    monkeypatch.setattr(response, "aclose", close)
    return close_calls


def _is_nonempty_body(message: Any) -> bool:
    """Return whether one ASGI message carries a response body chunk."""
    if message.get("type") != "http.response.body":
        return False
    return bool(message.get("body"))


async def test_forward_stream_holds_pool_until_eof(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """A streamed relay lease spans headers, body EOF, and source close."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        b"second",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(
        headers={"content-type": "application/json"},
        stream=source,
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))

    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )

    assert isinstance(response, StreamingResponse)
    held = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    assert held.in_use == 1
    assert held.started == 1
    assert source.closed is False

    assert await _body(response) == b"firstsecond"
    released = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    _assert_terminal_outcome(released, completed=1)
    _assert_source_closed_before_release(close_snapshots)
    assert source.closed is True


async def test_forward_stream_close_releases_pool_and_source(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Closing a partially consumed relay stream releases both resources."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        b"second",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(
        headers={"content-type": "application/json"},
        stream=source,
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))

    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    assert isinstance(response, StreamingResponse)

    iterator = cast(Any, response.body_iterator)
    assert await anext(iterator) == b"first"
    await iterator.aclose()

    released = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    _assert_terminal_outcome(released, cancelled=1)
    _assert_source_closed_before_release(close_snapshots)
    assert source.closed is True
    assert store.query()[0].error_type == "client_disconnected"


async def test_forward_stream_close_before_first_chunk_releases_ownership(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Pre-iteration close eagerly releases every streamed owner once."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"unread",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    queued_response = outbound_runtime.transport.responses[-1]
    assert not isinstance(queued_response, Exception)
    response_close_calls = _record_response_closes(
        monkeypatch, queued_response
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )

    assert isinstance(response, StreamingResponse)
    assert (
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM).in_use
        == 1
    )
    iterator = cast(Any, response.body_iterator)
    current_task = asyncio.current_task()
    assert current_task is not None
    cancelling_before_close = current_task.cancelling()
    await asyncio.wait_for(
        asyncio.gather(iterator.aclose(), iterator.aclose()),
        timeout=_TEST_TIMEOUT_SECONDS,
    )
    assert current_task.cancelling() == cancelling_before_close

    released = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    _assert_terminal_outcome(released, cancelled=1)
    _assert_source_closed_before_release(close_snapshots)
    assert source.closed is True
    assert len(response_close_calls) == 1
    assert store.query()[0].error_type == "client_disconnected"
    await asyncio.wait_for(
        aclose_outbound_runtime(), timeout=_TEST_TIMEOUT_SECONDS
    )


async def test_forward_stream_asgi_disconnect_before_first_byte_releases(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """An ASGI disconnect while awaiting byte one cancels every owner."""
    entered = asyncio.Event()
    release = asyncio.Event()
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"late",
        entered=entered,
        release=release,
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        await _wait_for_event(entered)
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        sent.append(message)

    try:
        await asyncio.wait_for(
            response(relay_request_scope(), receive, send),
            timeout=_TEST_TIMEOUT_SECONDS,
        )
    finally:
        release.set()

    assert sent[0]["type"] == "http.response.start"
    _assert_terminal_outcome(
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
        cancelled=1,
    )
    _assert_source_closed_before_release(close_snapshots)
    assert source.closed is True
    assert store.query()[0].error_type == "client_disconnected"
    await asyncio.wait_for(
        aclose_outbound_runtime(), timeout=_TEST_TIMEOUT_SECONDS
    )


async def test_forward_stream_asgi_disconnect_after_first_byte_releases(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """A disconnect during downstream send closes every streamed owner."""
    first_body_sent = asyncio.Event()
    release_send = asyncio.Event()
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        b"second",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    queued_response = outbound_runtime.transport.responses[-1]
    assert not isinstance(queued_response, Exception)
    response_close_calls = _record_response_closes(
        monkeypatch, queued_response
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    assert isinstance(response, StreamingResponse)

    async def receive() -> dict[str, str]:
        await _wait_for_event(first_body_sent)
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        if _is_nonempty_body(message):
            first_body_sent.set()
            await _wait_for_event(release_send)

    iterator = cast(Any, response.body_iterator)
    try:
        await asyncio.wait_for(
            response(relay_request_scope(), receive, send),
            timeout=_TEST_TIMEOUT_SECONDS,
        )

        _assert_terminal_outcome(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
            cancelled=1,
        )
        _assert_source_closed_before_release(close_snapshots)
        assert source.closed is True
        assert len(response_close_calls) == 1
        assert store.query()[0].error_type == "client_disconnected"
        await asyncio.wait_for(
            aclose_outbound_runtime(), timeout=_TEST_TIMEOUT_SECONDS
        )
    finally:
        release_send.set()
        await asyncio.wait_for(
            iterator.aclose(), timeout=_TEST_TIMEOUT_SECONDS
        )


async def test_forward_stream_asgi_send_error_releases_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """An ASGI 2.4 send failure closes ownership before reraising."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        b"second",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    queued_response = outbound_runtime.transport.responses[-1]
    assert not isinstance(queued_response, Exception)
    response_close_calls = _record_response_closes(
        monkeypatch, queued_response
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    assert isinstance(response, StreamingResponse)
    scope = relay_request_scope()
    scope["asgi"] = {"spec_version": "2.4"}

    async def receive() -> dict[str, str]:
        raise AssertionError("ASGI 2.4 streaming must not poll receive")

    async def send(message: Any) -> None:
        if _is_nonempty_body(message):
            raise OSError("downstream send closed")

    iterator = cast(Any, response.body_iterator)
    try:
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(
                response(scope, receive, send),
                timeout=_TEST_TIMEOUT_SECONDS,
            )

        _assert_terminal_outcome(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
            cancelled=1,
        )
        _assert_source_closed_before_release(close_snapshots)
        assert source.closed is True
        assert len(response_close_calls) == 1
        assert store.query()[0].error_type == "client_disconnected"
    finally:
        await asyncio.wait_for(
            iterator.aclose(), timeout=_TEST_TIMEOUT_SECONDS
        )


async def test_forward_stream_asgi_caller_cancel_releases_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Caller cancellation closes response ownership and still propagates."""
    first_body_sent = asyncio.Event()
    release_send = asyncio.Event()
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        b"second",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    queued_response = outbound_runtime.transport.responses[-1]
    assert not isinstance(queued_response, Exception)
    response_close_calls = _record_response_closes(
        monkeypatch, queued_response
    )
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    assert isinstance(response, StreamingResponse)
    scope = relay_request_scope()
    scope["asgi"] = {"spec_version": "2.4"}

    async def receive() -> dict[str, str]:
        raise AssertionError("ASGI 2.4 streaming must not poll receive")

    async def send(message: Any) -> None:
        if _is_nonempty_body(message):
            first_body_sent.set()
            await _wait_for_event(release_send)

    task = asyncio.create_task(response(scope, receive, send))
    try:
        await _wait_for_event(first_body_sent)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=_TEST_TIMEOUT_SECONDS)

        _assert_terminal_outcome(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
            cancelled=1,
        )
        _assert_source_closed_before_release(close_snapshots)
        assert source.closed is True
        assert len(response_close_calls) == 1
        assert store.query()[0].error_type == "client_disconnected"
    finally:
        release_send.set()
        await _settle_task(task)
        await asyncio.wait_for(
            cast(Any, response.body_iterator).aclose(),
            timeout=_TEST_TIMEOUT_SECONDS,
        )


async def test_forward_stream_closes_source_before_releasing_pool_on_eof(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Normal EOF closes the upstream source while its pool remains held."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"body",
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=RelayAuditStore(str(tmp_path / "relay.sqlite")),
    )

    assert isinstance(response, StreamingResponse)
    assert await _body(response) == b"body"
    _assert_source_closed_before_release(close_snapshots)
    _assert_terminal_outcome(
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
        completed=1,
    )


async def test_forward_stream_upstream_failure_closes_before_pool_release(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """A mid-body upstream failure audits, closes, then releases capacity."""
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"first",
        failure=OSError("upstream-body-marker"),
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )

    assert isinstance(response, StreamingResponse)
    assert await _body(response) == b"first"
    _assert_source_closed_before_release(close_snapshots)
    _assert_terminal_outcome(
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
        failed=1,
    )
    assert store.query()[0].error_type == "OSError"


async def test_forward_stream_deadline_closes_before_failed_pool_release(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """A body deadline audits partial output, closes, then fails the lease."""
    entered = asyncio.Event()
    release = asyncio.Event()
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"late",
        entered=entered,
        release=release,
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    monkeypatch.setattr(
        forward_module,
        "_relay_timeout_seconds",
        lambda **_kwargs: 0.01,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )

    assert isinstance(response, StreamingResponse)
    assert await _body(response) == b""
    assert entered.is_set()
    _assert_source_closed_before_release(close_snapshots)
    _assert_terminal_outcome(
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM),
        failed=1,
    )
    assert source.closed is True
    assert store.query()[0].error_type == "deadline_exceeded"


async def test_forward_stream_cancel_closes_source_and_releases_pool(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Cancelling downstream body iteration closes source and target lease."""
    entered = asyncio.Event()
    release = asyncio.Event()
    close_snapshots: list[Any] = []
    source = ControlledByteStream(
        b"late",
        entered=entered,
        release=release,
        on_close=lambda: close_snapshots.append(
            outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
        ),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    store = RelayAuditStore(str(tmp_path / "relay.sqlite"))
    response = await forward_relay_request(
        request=_request(),
        body=b"{}",
        upstream=_upstream(),
        principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
        audit_store=store,
    )
    assert isinstance(response, StreamingResponse)
    iterator = cast(Any, response.body_iterator)
    consumer = asyncio.create_task(anext(iterator))

    try:
        await _wait_for_event(entered)
        assert (
            outbound_runtime.runtime.pools.snapshot(
                OutboundPoolName.LLM
            ).in_use
            == 1
        )
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, timeout=_TEST_TIMEOUT_SECONDS)
    finally:
        release.set()
        await _settle_task(consumer)
        await asyncio.wait_for(
            iterator.aclose(), timeout=_TEST_TIMEOUT_SECONDS
        )

    released = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    _assert_terminal_outcome(released, cancelled=1)
    _assert_source_closed_before_release(close_snapshots)
    assert source.closed is True
    assert store.query()[0].error_type == "client_disconnected"


async def test_operator_relay_finishes_iam_before_target_pool(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Credential injection completes before target capacity is acquired."""
    iam_entered = asyncio.Event()
    release_iam = asyncio.Event()

    async def blocked_inject() -> dict[str, str]:
        iam_entered.set()
        await _wait_for_event(release_iam)
        return {"X-Auth-Token": "operator-token"}

    outbound_runtime.transport.enqueue(content=b"{}")
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )
    task = asyncio.create_task(
        forward_relay_request(
            request=_request(),
            body=b"{}",
            upstream=RelayUpstream(
                url="https://upstream.test/analysis",
                error_mode=RelayErrorMode.ENVELOPE,
                service="analysis",
                inject_headers=blocked_inject,
                pool=OutboundPoolName.ANALYSIS_CONTROL,
            ),
            principal=ApiPrincipal(user_id="u1", key_prefix="ptm_test"),
            audit_store=RelayAuditStore(str(tmp_path / "relay.sqlite")),
        )
    )

    try:
        await _wait_for_event(iam_entered)
        snapshot = outbound_runtime.runtime.pools.snapshot(
            OutboundPoolName.ANALYSIS_CONTROL
        )
        assert snapshot.started == 0
        assert snapshot.in_use == 0
        assert snapshot.waiting == 0

        release_iam.set()
        response = await asyncio.wait_for(task, timeout=_TEST_TIMEOUT_SECONDS)
    finally:
        release_iam.set()
        await _settle_task(task)
    assert response.status_code == 200
    final = outbound_runtime.runtime.pools.snapshot(
        OutboundPoolName.ANALYSIS_CONTROL
    )
    assert final.started == 1
    assert final.in_use == 0


async def test_child_and_operator_relay_pool_observability_is_private(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Relay calls expose fixed pool counters without caller-owned markers."""
    markers = (
        "url-marker",
        "credential-marker",
        "header-marker",
        "query-marker",
        "task-marker",
        "response-body-marker",
        "exception-marker",
    )
    caplog.set_level(
        logging.INFO,
        logger="mcp_server_phytomni.runtime.outbound.registry",
    )
    monkeypatch.setattr(
        logging.getLogger("mcp_server_phytomni"), "propagate", True
    )
    child = RelayClient(
        base_url="https://url-marker.invalid",
        api_key=SecretStr("credential-marker"),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )
    outbound_runtime.transport.enqueue(
        content=b'{"response-body-marker":true}'
    )
    await child.get_json(
        "analysis/task-marker",
        pool=OutboundPoolName.ANALYSIS_STATUS,
        options=RelayRequestOptions(message="safe child relay error"),
        query={"query-marker": "header-marker"},
    )

    source = ControlledByteStream(
        b"response-body-marker",
        failure=OSError("exception-marker"),
    )
    outbound_runtime.transport.enqueue(stream=source)
    monkeypatch.setattr(
        forward_module,
        "current_outbound_runtime",
        lambda: outbound_runtime.runtime,
    )

    async def inject() -> dict[str, str]:
        return {"X-Secret": "credential-marker header-marker"}

    response = await forward_relay_request(
        request=_request(),
        body=b'{"query":"query-marker","task":"task-marker"}',
        upstream=RelayUpstream(
            url="https://url-marker.invalid/upstream",
            error_mode=RelayErrorMode.TRANSPARENT,
            service="analysis",
            inject_headers=inject,
            pool=OutboundPoolName.ANALYSIS_STATUS,
            operation="task-marker",
        ),
        principal=ApiPrincipal(
            user_id="credential-marker",
            key_prefix="header-marker",
        ),
        audit_store=RelayAuditStore(str(tmp_path / "relay.sqlite")),
    )
    assert isinstance(response, StreamingResponse)
    assert await _body(response) == b"response-body-marker"

    observations = "\n".join(
        [
            *(
                repr(snapshot)
                for snapshot in outbound_runtime.runtime.pools.snapshots()
            ),
            *(
                record.getMessage()
                for record in caplog.records
                if record.name.startswith(
                    "mcp_server_phytomni.runtime.outbound"
                )
            ),
        ]
    )
    for marker in markers:
        assert marker not in observations
    assert (
        "name=<OutboundPoolName.ANALYSIS_STATUS: 'analysis_status'>"
        in observations
    )
    assert "pool=analysis_status capacity=" in observations
