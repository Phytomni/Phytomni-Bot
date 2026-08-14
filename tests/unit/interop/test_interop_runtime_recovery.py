# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Recovery contracts for process-owned Interop MCP resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from anyio import ClosedResourceError
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, ErrorData

from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.models import MCPStreamableHttpTarget
from mcp_server_phytomni.interop.registry import InteropRegistry
from mcp_server_phytomni.interop.runtime import (
    InteropResourceFactories,
    InteropResourceRuntime,
)
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolRegistry,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class _McpHarness:
    """Observable resources behind one streamable-HTTP MCP target."""

    runtime: InteropResourceRuntime
    clients: list[httpx.AsyncClient]
    sessions: list[Any]
    closed_sessions: list[Any]


def _mcp_harness(
    *,
    capacity: int = 2,
    initialization_started: asyncio.Event | None = None,
    release_initialization: asyncio.Event | None = None,
    session_close_failures: int = 0,
) -> _McpHarness:
    """Build one runtime whose client and session identities are observable."""
    target = MCPStreamableHttpTarget.model_validate(
        {
            "id": "peer-http",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://peer-http.example.test/v1/mcp",
            "allowed_tools": ["search_genes"],
        }
    )
    capacities = {name: 0 for name in OutboundPoolName}
    capacities[OutboundPoolName.INTEROP] = capacity
    pools = OutboundPoolRegistry(capacities, wait_warn_seconds=1.0)
    clients: list[httpx.AsyncClient] = []
    sessions: list[Any] = []
    closed_sessions: list[Any] = []
    remaining_close_failures = session_close_failures

    def client_factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient()
        clients.append(client)
        return client

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        yield object(), object(), lambda: None

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        nonlocal remaining_close_failures
        session = SimpleNamespace()

        async def initialize() -> None:
            if initialization_started is not None:
                initialization_started.set()
                assert release_initialization is not None
                await release_initialization.wait()

        session.initialize = initialize
        sessions.append(session)
        try:
            yield session
        finally:
            closed_sessions.append(session)
            if remaining_close_failures > 0:
                remaining_close_failures -= 1
                raise RuntimeError("session close failed")

    runtime = InteropResourceRuntime(
        InteropRegistry(enabled=True, _targets={target.id: target}),
        SensitiveConfig.model_construct(),
        pools,
        factories=InteropResourceFactories(
            http=client_factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )
    return _McpHarness(runtime, clients, sessions, closed_sessions)


async def _nothing(_session: Any) -> None:
    """Complete one MCP operation without inspecting the session."""


@pytest.mark.asyncio
async def test_http_eviction_close_error_does_not_mask_transport_failure() -> (
    None
):
    """Resource cleanup cannot replace the operation's typed failure."""
    harness = _mcp_harness()

    async def succeeds(_client: httpx.AsyncClient) -> None:
        return None

    await harness.runtime.run_http("peer-http", succeeds)
    client = harness.clients[0]
    original_close = client.aclose

    async def failing_close() -> None:
        await original_close()
        raise RuntimeError("close failed")

    setattr(client, "aclose", failing_close)

    async def transport_failure(_client: httpx.AsyncClient) -> None:
        raise httpx.ConnectError("peer closed")

    with pytest.raises(httpx.ConnectError):
        await harness.runtime.run_http("peer-http", transport_failure)

    assert harness.runtime.http_keys == frozenset()
    assert client.is_closed
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_cancellation_waits_for_http_eviction_cleanup() -> None:
    """Cancellation propagates only after the failed client is closed."""
    harness = _mcp_harness()

    async def succeeds(_client: httpx.AsyncClient) -> None:
        return None

    await harness.runtime.run_http("peer-http", succeeds)
    client = harness.clients[0]
    original_close = client.aclose
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def blocking_close() -> None:
        close_started.set()
        await release_close.wait()
        await original_close()

    setattr(client, "aclose", blocking_close)

    async def transport_failure(_client: httpx.AsyncClient) -> None:
        raise httpx.ConnectError("peer closed")

    task = asyncio.create_task(
        harness.runtime.run_http("peer-http", transport_failure)
    )
    await asyncio.wait_for(close_started.wait(), timeout=1.0)
    task.cancel()
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release_close.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    assert client.is_closed
    assert harness.runtime.http_keys == frozenset()
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_concurrent_first_use_publishes_one_mcp_session() -> None:
    """Same-target first callers wait for one initialized session identity."""
    initialization_started = asyncio.Event()
    release_initialization = asyncio.Event()
    harness = _mcp_harness(
        initialization_started=initialization_started,
        release_initialization=release_initialization,
    )
    sessions_used: list[Any] = []

    async def operation(session: Any) -> None:
        sessions_used.append(session)

    first = asyncio.create_task(
        harness.runtime.run_mcp("peer-http", operation)
    )
    await asyncio.wait_for(initialization_started.wait(), timeout=1.0)
    second = asyncio.create_task(
        harness.runtime.run_mcp("peer-http", operation)
    )
    try:
        await asyncio.sleep(0)
        assert len(harness.sessions) == 1
    finally:
        release_initialization.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1.0)

    assert sessions_used == [harness.sessions[0], harness.sessions[0]]
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_closed_mcp_session_is_evicted_and_next_call_rebuilds() -> None:
    """A typed MCP channel closure invalidates its exact cached session."""
    harness = _mcp_harness(session_close_failures=1)
    await harness.runtime.run_mcp("peer-http", _nothing)
    first_session = harness.sessions[0]

    async def closed_operation(_session: Any) -> None:
        raise ClosedResourceError

    with pytest.raises(ClosedResourceError):
        await harness.runtime.run_mcp("peer-http", closed_operation)

    assert harness.runtime.keys == frozenset()
    assert harness.runtime.http_keys == frozenset()
    assert harness.clients[0].is_closed
    assert harness.closed_sessions == [first_session]

    await harness.runtime.run_mcp("peer-http", _nothing)
    assert len(harness.clients) == 2
    assert len(harness.sessions) == 2
    await harness.runtime.aclose()
    await harness.runtime.aclose()
    assert harness.closed_sessions == harness.sessions


@pytest.mark.asyncio
async def test_mcp_disconnect_evicts_before_next_call_rebuilds() -> None:
    """The MCP SDK's typed disconnect invalidates only the failed session."""
    harness = _mcp_harness()
    await harness.runtime.run_mcp("peer-http", _nothing)
    first_session = harness.sessions[0]
    operation_calls = 0

    async def connection_closed(_session: Any) -> None:
        nonlocal operation_calls
        operation_calls += 1
        raise McpError(
            ErrorData(
                code=CONNECTION_CLOSED,
                message="Connection closed",
            )
        )

    with pytest.raises(McpError) as caught:
        await harness.runtime.run_mcp("peer-http", connection_closed)

    assert caught.value.error.code == CONNECTION_CLOSED
    assert operation_calls == 1
    assert harness.runtime.keys == frozenset()
    assert harness.runtime.http_keys == frozenset()
    assert harness.clients[0].is_closed
    assert harness.closed_sessions == [first_session]

    await harness.runtime.run_mcp("peer-http", _nothing)
    assert len(harness.clients) == 2
    assert len(harness.sessions) == 2
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_protocol_error_preserves_healthy_shared_session() -> None:
    """A non-disconnect MCP error does not poison the cached session."""
    harness = _mcp_harness()
    await harness.runtime.run_mcp("peer-http", _nothing)
    shared_session = harness.sessions[0]

    async def protocol_error(_session: Any) -> None:
        raise McpError(
            ErrorData(
                code=-32603,
                message="remote operation failed",
            )
        )

    with pytest.raises(McpError) as caught:
        await harness.runtime.run_mcp("peer-http", protocol_error)

    assert caught.value.error.code == -32603
    assert harness.runtime.keys == {("peer-http", "streamable_http")}
    assert harness.runtime.http_keys == {"peer-http"}
    assert not harness.closed_sessions
    assert not harness.clients[0].is_closed

    sessions_used: list[Any] = []

    async def record_session(session: Any) -> None:
        sessions_used.append(session)

    await harness.runtime.run_mcp("peer-http", record_session)
    assert sessions_used == [shared_session]
    assert len(harness.sessions) == 1
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_cancellation_preserves_shared_session() -> None:
    """Caller cancellation releases its lease without closing the session."""
    harness = _mcp_harness()
    await harness.runtime.run_mcp("peer-http", _nothing)
    shared_session = harness.sessions[0]
    entered = asyncio.Event()

    async def blocked_operation(_session: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        harness.runtime.run_mcp("peer-http", blocked_operation)
    )
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)

    assert harness.runtime.keys == {("peer-http", "streamable_http")}
    assert harness.runtime.http_keys == {"peer-http"}
    assert not harness.closed_sessions
    assert not harness.clients[0].is_closed

    sessions_used: list[Any] = []

    async def record_session(session: Any) -> None:
        sessions_used.append(session)

    await harness.runtime.run_mcp("peer-http", record_session)
    assert sessions_used == [shared_session]
    assert len(harness.clients) == 1
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_rebuilds_before_use_when_shared_http_is_closed() -> None:
    """A closed streamable transport cannot retain its cached session."""
    harness = _mcp_harness()
    await harness.runtime.run_mcp("peer-http", _nothing)
    first_session = harness.sessions[0]
    await harness.clients[0].aclose()

    await harness.runtime.run_mcp("peer-http", _nothing)

    assert len(harness.clients) == 2
    assert harness.clients[0] is not harness.clients[1]
    assert not harness.clients[1].is_closed
    assert harness.closed_sessions == [first_session]
    await harness.runtime.aclose()
