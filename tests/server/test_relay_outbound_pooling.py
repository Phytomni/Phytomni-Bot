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
from starlette.requests import Request
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
from mcp_server_phytomni.runtime.outbound import OutboundPoolName

pytestmark = pytest.mark.server


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


async def test_forward_stream_holds_pool_until_eof(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """A streamed relay lease spans headers, body EOF, and source close."""
    source = ControlledByteStream(b"first", b"second")
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
    assert released.in_use == 0
    assert released.completed == 1
    assert source.closed is True


async def test_forward_stream_close_releases_pool_and_source(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Closing a partially consumed relay stream releases both resources."""
    source = ControlledByteStream(b"first", b"second")
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
    assert released.in_use == 0
    assert released.completed == 1
    assert source.closed is True
    assert store.query()[0].error_type == "client_disconnected"


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
    assert len(close_snapshots) == 1
    assert close_snapshots[0].in_use == 1
    assert (
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM).in_use
        == 0
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
    assert len(close_snapshots) == 1
    assert close_snapshots[0].in_use == 1
    assert (
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM).in_use
        == 0
    )
    assert store.query()[0].error_type == "OSError"


async def test_forward_stream_cancel_closes_source_and_releases_pool(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    tmp_path: Path,
) -> None:
    """Cancelling downstream body iteration closes source and target lease."""
    entered = asyncio.Event()
    release = asyncio.Event()
    source = ControlledByteStream(b"late", entered=entered, release=release)
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

    await entered.wait()
    assert (
        outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM).in_use
        == 1
    )
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await iterator.aclose()

    released = outbound_runtime.runtime.pools.snapshot(OutboundPoolName.LLM)
    assert released.in_use == 0
    assert released.cancelled == 0
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
        await release_iam.wait()
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

    await iam_entered.wait()
    snapshot = outbound_runtime.runtime.pools.snapshot(
        OutboundPoolName.ANALYSIS_CONTROL
    )
    assert snapshot.started == 0
    assert snapshot.in_use == 0
    assert snapshot.waiting == 0

    release_iam.set()
    response = await task
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
