# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Relay final-service mapping and streamed lease-lifetime tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.responses import StreamingResponse
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
