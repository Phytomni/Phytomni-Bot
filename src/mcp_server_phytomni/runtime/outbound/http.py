# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Fixed HTTP profiles bound to logical outbound request pools."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from ...config.defaults import ServerConfig
from .models import OutboundHttpProfile, OutboundPoolName
from .registry import OutboundPoolRegistry

type VerifyArg = bool | ssl.SSLContext
type AsyncClientFactory = Callable[..., httpx.AsyncClient]


def _resolve_verify(config: ServerConfig) -> VerifyArg:
    """Return the centrally configured HTTPX TLS verification argument."""
    if not config.TLS_VERIFY:
        return False
    if config.CA_BUNDLE:
        return ssl.create_default_context(cafile=config.CA_BUNDLE)
    return True


@dataclass(frozen=True, slots=True)
class OutboundHttpFactories:
    """Construction seams for the two process-owned HTTP profiles."""

    trusted: AsyncClientFactory = httpx.AsyncClient
    direct_upstream: AsyncClientFactory = httpx.AsyncClient


class BoundAsyncRequestClient:
    """Bind every buffered or streamed attempt to one logical pool."""

    def __init__(
        self,
        pools: OutboundPoolRegistry,
        pool: OutboundPoolName,
        profile_client: httpx.AsyncClient,
    ) -> None:
        self._pools = pools
        self._pool = pool
        self._profile_client = profile_client

    @property
    def profile_client(self) -> httpx.AsyncClient:
        """Expose the owned profile client for identity-only diagnostics."""
        return self._profile_client

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run one fully buffered, status-classified leased attempt."""
        request = self._profile_client.build_request(method, url, **kwargs)
        async with self._pools.lease(self._pool):
            response = await self._profile_client.send(
                request,
                stream=True,
            )
            try:
                await response.aread()
                response.raise_for_status()
                return response
            finally:
                await response.aclose()


class OutboundHttpRuntime:
    """Own exactly two HTTPX profiles and bind them to typed pools."""

    def __init__(
        self,
        pools: OutboundPoolRegistry,
        *,
        trusted: httpx.AsyncClient,
        direct_upstream: httpx.AsyncClient,
    ) -> None:
        self._pools = pools
        self.trusted = trusted
        self.direct_upstream = direct_upstream
        self._close_task: asyncio.Task[None] | None = None

    def _profile_client(
        self, profile: OutboundHttpProfile
    ) -> httpx.AsyncClient:
        """Return the fixed client selected by a typed profile."""
        if profile is OutboundHttpProfile.TRUSTED:
            return self.trusted
        if profile is OutboundHttpProfile.DIRECT_UPSTREAM:
            return self.direct_upstream
        raise ValueError(f"unsupported outbound HTTP profile: {profile}")

    def for_pool(
        self,
        pool: OutboundPoolName,
        *,
        profile: OutboundHttpProfile = OutboundHttpProfile.TRUSTED,
    ) -> BoundAsyncRequestClient:
        """Bind one fixed HTTP profile to one service pool."""
        return BoundAsyncRequestClient(
            self._pools,
            pool,
            self._profile_client(profile),
        )

    @asynccontextmanager
    async def stream(
        self,
        pool: OutboundPoolName,
        method: str,
        url: str,
        *,
        profile: OutboundHttpProfile = OutboundHttpProfile.TRUSTED,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        """Hold one typed lease through a streaming response lifetime."""
        response_context = self._profile_client(profile).stream(
            method,
            url,
            **kwargs,
        )
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self._pools.lease(pool))
            response = await stack.enter_async_context(response_context)
            yield response

    async def _close_resources(self) -> None:
        """Close fixed profiles in reverse construction order."""
        try:
            await self.direct_upstream.aclose()
        finally:
            await self.trusted.aclose()

    async def aclose(self) -> None:
        """Close both owned profiles exactly once."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_resources())
        await asyncio.shield(self._close_task)


def build_outbound_http_runtime(
    config: ServerConfig,
    pools: OutboundPoolRegistry,
    stack: AsyncExitStack,
    *,
    factories: OutboundHttpFactories | None = None,
) -> OutboundHttpRuntime:
    """Construct both profiles privately and register reverse cleanup."""
    resolved_factories = factories or OutboundHttpFactories()
    limits = httpx.Limits(
        max_connections=config.HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=config.HTTP_MAX_KEEPALIVE,
    )
    common = {
        "verify": _resolve_verify(config),
        "timeout": None,
        "limits": limits,
    }
    trusted: httpx.AsyncClient | None = None
    direct_upstream: httpx.AsyncClient | None = None
    runtime: OutboundHttpRuntime | None = None

    async def close_resources() -> None:
        """Close partial construction or the published runtime."""
        if runtime is not None:
            await runtime.aclose()
            return
        if direct_upstream is not None:
            await direct_upstream.aclose()
        if trusted is not None:
            await trusted.aclose()

    stack.push_async_callback(close_resources)
    trusted = resolved_factories.trusted(**common)
    direct_upstream = resolved_factories.direct_upstream(
        **common,
        trust_env=False,
    )
    runtime = OutboundHttpRuntime(
        pools,
        trusted=trusted,
        direct_upstream=direct_upstream,
    )
    return runtime
