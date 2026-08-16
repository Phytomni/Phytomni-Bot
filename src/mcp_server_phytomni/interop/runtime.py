# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Process-owned, bounded resources for operator-approved Interop targets."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar

import httpx
from a2a.client.errors import A2AClientError as A2ASDKClientError
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from ..config.defaults import ApiConfig
from ..config.settings import SensitiveConfig
from ..runtime.outbound.lifecycle import current_outbound_runtime
from ..runtime.outbound.models import OutboundPoolName
from ..runtime.outbound.registry import OutboundPoolRegistry
from .http_transport import InteropHTTPError, httpx_client_factory
from .models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from .registry import (
    InteropRegistry,
    InteropRegistryError,
    load_interop_registry,
)
from .security import AsyncDNSResolver, resolve_host

T = TypeVar("T")

type InteropHttpFactory = Callable[..., httpx.AsyncClient]
type InteropHttpOperation[T] = Callable[[httpx.AsyncClient], Awaitable[T]]
type InteropMcpOperation[T] = Callable[[ClientSession], Awaitable[T]]
type InteropA2aFactory = Callable[[httpx.AsyncClient], object]
type InteropA2aOperation[T] = Callable[[object], AsyncIterator[T]]
type InteropA2aClose = Callable[[object], Awaitable[None]]

_RESOURCE_FAILURES = (
    BrokenResourceError,
    ClosedResourceError,
    ConnectionError,
    EOFError,
    EndOfStream,
    OSError,
    TimeoutError,
    httpx.TransportError,
    InteropHTTPError,
)
_A2A_RESOURCE_FAILURES = (*_RESOURCE_FAILURES, A2ASDKClientError)


async def _finish_cleanup(awaitable: Awaitable[object]) -> None:
    """Finish cleanup without masking failure or dropping cancellation."""
    cleanup = asyncio.ensure_future(awaitable)
    completion = asyncio.create_task(asyncio.wait({cleanup}))
    cancelled = False
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            cancelled = True
    with suppress(Exception, asyncio.CancelledError):
        cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


class InteropResourceRuntimeError(RuntimeError):
    """Raised when a process-owned Interop resource cannot be used."""


@dataclass(slots=True)
class _McpResource:
    """One initialized MCP session and the contexts that own it."""

    session: ClientSession
    stack: AsyncExitStack
    http_client: httpx.AsyncClient | None = None


@dataclass(slots=True)
class _A2AResource:
    """One target-owned A2A SDK client over the shared HTTP client."""

    client: object
    http_client: httpx.AsyncClient
    close: InteropA2aClose


@dataclass(frozen=True, slots=True)
class InteropResourceFactories:
    """Construction seams for process-owned Interop resources."""

    http: InteropHttpFactory = httpx_client_factory
    resolver: AsyncDNSResolver = resolve_host
    streamable: Callable[..., Any] = streamable_http_client
    stdio: Callable[..., Any] = stdio_client
    session: Callable[..., Any] = ClientSession


@dataclass(slots=True)
class _InteropResourceState:
    """Mutable maps and shutdown state for one process runtime."""

    http_clients: dict[str, httpx.AsyncClient]
    a2a_resources: dict[tuple[str, str], _A2AResource]
    mcp_resources: dict[tuple[str, str], _McpResource]
    locks: dict[tuple[str, str], asyncio.Lock]
    close_lock: asyncio.Lock
    closing: bool = False
    closed: bool = False


class InteropResourceRuntime:
    """Own bounded HTTP clients and initialized MCP sessions by target id.

    The registry is immutable and operator-owned.  Callers can select only a
    validated target id; URLs, commands, headers, and credentials never form
    resource keys.  The logical ``interop`` lease surrounds each actual
    operation and each remote MCP initialization, not target validation or
    local client construction.
    """

    def __init__(
        self,
        registry: InteropRegistry,
        sensitive_config: SensitiveConfig,
        pools: OutboundPoolRegistry,
        *,
        factories: InteropResourceFactories | None = None,
    ) -> None:
        """Store trusted configuration and empty bounded resource maps."""
        self._registry = registry
        self._sensitive_config = sensitive_config
        self._pools = pools
        self._factories = factories or InteropResourceFactories()
        self._state = _InteropResourceState(
            http_clients={},
            a2a_resources={},
            mcp_resources={},
            locks={},
            close_lock=asyncio.Lock(),
        )

    @property
    def keys(self) -> frozenset[tuple[str, str]]:
        """Return target/transport keys currently holding MCP resources."""
        return frozenset(self._state.mcp_resources)

    @property
    def http_keys(self) -> frozenset[str]:
        """Return target ids currently holding HTTP clients."""
        return frozenset(self._state.http_clients)

    @property
    def a2a_keys(self) -> frozenset[tuple[str, str]]:
        """Return target/transport keys holding reusable A2A clients."""
        return frozenset(self._state.a2a_resources)

    def _ensure_open(self) -> None:
        """Reject new work after shutdown begins."""
        if self._state.closing or self._state.closed:
            raise InteropResourceRuntimeError(
                "interop resource runtime is closing"
            )

    def _target(self, target_id: str) -> InteropTarget:
        """Resolve one canonical target id before any resource lookup."""
        try:
            return self._registry.require_target(target_id)
        except InteropRegistryError:
            raise InteropResourceRuntimeError(
                "unknown outbound interop target id"
            ) from None

    def _lock_for(self, key: tuple[str, str]) -> asyncio.Lock:
        """Return one per-key creation/eviction lock."""
        lock = self._state.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._state.locks[key] = lock
        return lock

    def _http_target(self, target_id: str) -> HTTPTarget:
        """Resolve an HTTP-capable target without accepting a URL."""
        target = self._target(target_id)
        if isinstance(target, MCPStdioTarget):
            raise InteropResourceRuntimeError(
                "outbound interop target is not HTTP-capable"
            )
        return target

    def _a2a_target(self, target_id: str) -> A2ATarget:
        """Resolve one validated A2A target before resource lookup."""
        target = self._target(target_id)
        if not isinstance(target, A2ATarget):
            raise InteropResourceRuntimeError(
                "outbound interop target is not an A2A target"
            )
        return target

    async def _get_http_client(
        self,
        target_id: str,
    ) -> httpx.AsyncClient:
        """Create one hardened HTTP client per validated target id."""
        self._http_target(target_id)
        key = (target_id, "http")
        lock = self._lock_for(key)
        async with lock:
            self._ensure_open()
            client = self._state.http_clients.get(target_id)
            if client is not None:
                if not client.is_closed:
                    return client
                self._state.http_clients.pop(target_id, None)
            client = self._factories.http(
                target_id,
                registry=self._registry,
                sensitive_config=self._sensitive_config,
                resolver=self._factories.resolver,
            )
            self._state.http_clients[target_id] = client
            return client

    async def _evict_http(
        self,
        target_id: str,
        client: httpx.AsyncClient,
    ) -> None:
        """Evict and close only the resource that failed."""
        key = (target_id, "http")
        lock = self._lock_for(key)
        async with lock:
            if self._state.http_clients.get(target_id) is not client:
                return
            self._state.http_clients.pop(target_id, None)
            if not client.is_closed:
                await _finish_cleanup(client.aclose())

    async def run_http(
        self,
        target_id: str,
        operation: InteropHttpOperation[T],
    ) -> T:
        """Run one buffered HTTP operation under the Interop lease."""
        self._ensure_open()
        self._http_target(target_id)
        client = await self._get_http_client(target_id)
        try:
            async with self._pools.lease(OutboundPoolName.INTEROP):
                return await operation(client)
        except _RESOURCE_FAILURES:
            await self._evict_http(target_id, client)
            raise

    async def stream_http(
        self,
        target_id: str,
        operation: Callable[[httpx.AsyncClient], AsyncIterator[T]],
    ) -> AsyncIterator[T]:
        """Stream one HTTP operation while retaining its Interop lease."""
        self._ensure_open()
        self._http_target(target_id)
        client = await self._get_http_client(target_id)
        try:
            async with self._pools.lease(OutboundPoolName.INTEROP):
                async for item in operation(client):
                    yield item
        except _RESOURCE_FAILURES:
            await self._evict_http(target_id, client)
            raise

    async def _get_a2a_resource(
        self,
        target_id: str,
        factory: InteropA2aFactory,
        close: InteropA2aClose,
    ) -> _A2AResource:
        """Create one SDK client per validated A2A target transport."""
        target = self._a2a_target(target_id)
        key = (target_id, target.transport)
        lock = self._lock_for(key)
        async with lock:
            self._ensure_open()
            resource = self._state.a2a_resources.get(key)
            if resource is not None:
                if not resource.http_client.is_closed:
                    return resource
                self._state.a2a_resources.pop(key, None)
                try:
                    await _finish_cleanup(resource.close(resource.client))
                finally:
                    await self._evict_http(target_id, resource.http_client)
            http_client = await self._get_http_client(target_id)
            resource = _A2AResource(
                client=factory(http_client),
                http_client=http_client,
                close=close,
            )
            self._state.a2a_resources[key] = resource
            return resource

    async def _evict_a2a(
        self,
        target_id: str,
        resource: _A2AResource,
    ) -> None:
        """Evict and close only the A2A resource that failed."""
        target = self._a2a_target(target_id)
        key = (target_id, target.transport)
        lock = self._lock_for(key)
        async with lock:
            if self._state.a2a_resources.get(key) is not resource:
                return
            self._state.a2a_resources.pop(key, None)
            try:
                await _finish_cleanup(resource.close(resource.client))
            finally:
                await self._evict_http(target_id, resource.http_client)

    async def stream_a2a(
        self,
        target_id: str,
        factory: InteropA2aFactory,
        close: InteropA2aClose,
        operation: InteropA2aOperation[T],
    ) -> AsyncIterator[T]:
        """Stream through one reusable A2A client under the Interop lease."""
        self._ensure_open()
        self._a2a_target(target_id)
        resource = await self._get_a2a_resource(target_id, factory, close)
        try:
            async with self._pools.lease(OutboundPoolName.INTEROP):
                async for item in operation(resource.client):
                    yield item
        except _A2A_RESOURCE_FAILURES:
            await self._evict_a2a(target_id, resource)
            raise

    async def _build_mcp_resource(
        self,
        target_id: str,
        target: MCPStdioTarget | MCPStreamableHttpTarget,
    ) -> _McpResource:
        """Open and initialize one target-owned MCP session."""
        stack = AsyncExitStack()
        http_client: httpx.AsyncClient | None = None
        try:
            if isinstance(target, MCPStreamableHttpTarget):
                http_client = await self._get_http_client(target_id)
                streams = await stack.enter_async_context(
                    self._factories.streamable(
                        target.url,
                        http_client=http_client,
                        terminate_on_close=True,
                    )
                )
                read_stream, write_stream, _ = streams
            else:
                params = StdioServerParameters(
                    command=target.command,
                    args=list(target.args),
                    env={
                        key: value
                        for key in target.env_keys
                        if (value := os.environ.get(key)) is not None
                    },
                )
                read_stream, write_stream = await stack.enter_async_context(
                    self._factories.stdio(params)
                )
            session = await stack.enter_async_context(
                self._factories.session(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=target.idle_timeout_seconds
                    ),
                )
            )
            await session.initialize()
            return _McpResource(session, stack, http_client)
        except BaseException:
            await _finish_cleanup(stack.aclose())
            raise

    async def _get_mcp_resource(self, target_id: str) -> _McpResource:
        """Create one initialized MCP resource per target transport key."""
        target = self._target(target_id)
        if not isinstance(target, (MCPStdioTarget, MCPStreamableHttpTarget)):
            raise InteropResourceRuntimeError(
                "outbound interop target is not an MCP target"
            )
        key = (target_id, target.transport)
        lock = self._lock_for(key)
        async with lock:
            self._ensure_open()
            resource = self._state.mcp_resources.get(key)
            if resource is not None:
                if (
                    resource.http_client is None
                    or not resource.http_client.is_closed
                ):
                    return resource
                self._state.mcp_resources.pop(key, None)
                try:
                    await _finish_cleanup(resource.stack.aclose())
                finally:
                    await self._evict_http(target_id, resource.http_client)
            http_client: httpx.AsyncClient | None = None
            if isinstance(target, MCPStreamableHttpTarget):
                # HTTP client construction is local preparation.  Open and
                # initialize the MCP transport only after capacity is held.
                http_client = await self._get_http_client(target_id)
            try:
                async with self._pools.lease(OutboundPoolName.INTEROP):
                    resource = await self._build_mcp_resource(
                        target_id, target
                    )
            except BaseException:
                if http_client is not None:
                    await self._evict_http(target_id, http_client)
                raise
            self._state.mcp_resources[key] = resource
            return resource

    async def _evict_mcp(
        self,
        target_id: str,
        resource: _McpResource,
    ) -> None:
        """Close one failed MCP session and remove its exact cache entry."""
        target = self._target(target_id)
        key = (target_id, target.transport)
        lock = self._lock_for(key)
        async with lock:
            if self._state.mcp_resources.get(key) is not resource:
                return
            self._state.mcp_resources.pop(key, None)
            try:
                await _finish_cleanup(resource.stack.aclose())
            finally:
                if (
                    isinstance(target, MCPStreamableHttpTarget)
                    and resource.http_client is not None
                ):
                    # Keep the MCP creation/eviction lock while removing the
                    # shared HTTP client. A replacement session must not
                    # capture the client that this failed session is closing.
                    await self._evict_http(target_id, resource.http_client)

    async def run_mcp(
        self,
        target_id: str,
        operation: InteropMcpOperation[T],
    ) -> T:
        """Run one MCP session operation under the Interop lease."""
        self._ensure_open()
        target = self._target(target_id)
        if not isinstance(target, (MCPStdioTarget, MCPStreamableHttpTarget)):
            raise InteropResourceRuntimeError(
                "outbound interop target is not an MCP target"
            )
        resource = await self._get_mcp_resource(target_id)
        try:
            async with self._pools.lease(OutboundPoolName.INTEROP):
                return await operation(resource.session)
        except McpError as exc:
            if exc.error.code == CONNECTION_CLOSED:
                await self._evict_mcp(target_id, resource)
            raise
        except _RESOURCE_FAILURES:
            await self._evict_mcp(target_id, resource)
            raise

    async def aclose(self) -> None:
        """Close sessions and SDK clients before their shared HTTP clients."""
        async with self._state.close_lock:
            if self._state.closed:
                return
            self._state.closing = True
            resources = tuple(self._state.mcp_resources.values())
            self._state.mcp_resources.clear()
            for mcp_resource in resources:
                await mcp_resource.stack.aclose()
            a2a_resources = tuple(self._state.a2a_resources.values())
            self._state.a2a_resources.clear()
            for a2a_resource in a2a_resources:
                await a2a_resource.close(a2a_resource.client)
            clients = tuple(self._state.http_clients.values())
            self._state.http_clients.clear()
            for client in clients:
                if not client.is_closed:
                    await client.aclose()
            self._state.closed = True


HTTPTarget = MCPStreamableHttpTarget | A2ATarget


def build_interop_resource_runtime(
    pools: OutboundPoolRegistry,
    *,
    api_config: ApiConfig | None = None,
    sensitive_config: SensitiveConfig | None = None,
    registry: InteropRegistry | None = None,
) -> InteropResourceRuntime:
    """Build the process-owned Interop runtime from the operator registry."""
    resolved_registry = registry or load_interop_registry(
        api_config=api_config,
        sensitive_config=sensitive_config,
    )
    resolved_sensitive = sensitive_config or SensitiveConfig.load()
    return InteropResourceRuntime(
        resolved_registry,
        resolved_sensitive,
        pools,
    )


def current_interop_runtime() -> InteropResourceRuntime | None:
    """Return the active Interop runtime, or ``None`` before initialization."""
    try:
        return current_outbound_runtime().interop
    except (ImportError, RuntimeError):
        return None


__all__ = [
    "InteropResourceRuntime",
    "InteropResourceFactories",
    "InteropResourceRuntimeError",
    "build_interop_resource_runtime",
    "current_interop_runtime",
]
