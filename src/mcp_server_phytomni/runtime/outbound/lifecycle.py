# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Atomic process lifecycle for logical pools and outbound resources."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx
from openai import AsyncOpenAI

from ...config.defaults import ServerConfig
from ...config.relay_mode import relay_mode_enabled
from ...config.settings import get_sensitive_config
from ...storage.obs_client import ObsClient
from ..async_utils import log_task_failure
from ..cleanup import aclose_cleanup_runtime
from .http import (
    BoundAsyncRequestClient,
    OutboundHttpFactories,
    OutboundHttpRuntime,
    _resolve_verify,
    build_outbound_http_runtime,
)
from .models import OutboundPoolName, OutboundPoolSnapshot
from .obs import (
    ObsClientFactory,
    ObsClientRuntime,
    build_obs_client_runtime,
)
from .registry import OutboundPoolRegistry

type OpenaiFactory = Callable[..., AsyncOpenAI]
type InteropFactory = Callable[[OutboundPoolRegistry], Any]

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...interop.runtime import InteropResourceRuntime


class OutboundRuntimeStateError(RuntimeError):
    """Raised when process-wide outbound runtime ownership is invalid."""


@dataclass(frozen=True, slots=True)
class OutboundResourceFactories:
    """Typed startup seams for process-owned outbound resources."""

    http: OutboundHttpFactories = field(default_factory=OutboundHttpFactories)
    openai: OpenaiFactory = AsyncOpenAI
    obs: ObsClientFactory = ObsClient
    interop: InteropFactory | None = None


@dataclass(slots=True)
class _OutboundRuntimeCloseState:
    """Mutable shutdown state kept separate from the runtime resources."""

    stack: AsyncExitStack
    closing: bool = False
    task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class OutboundRuntime:
    """Aggregate process-owned logical pools and HTTP resources."""

    pools: OutboundPoolRegistry
    http: OutboundHttpRuntime
    openai: AsyncOpenAI
    obs: ObsClientRuntime | None
    interop: InteropResourceRuntime | None
    _close_state: _OutboundRuntimeCloseState = field(repr=False)

    @property
    def closing(self) -> bool:
        """Return whether process shutdown has started."""
        return self._close_state.closing

    def begin_close(self) -> asyncio.Task[None]:
        """Start resource cleanup exactly once and return its shared task."""
        self._close_state.closing = True
        if self._close_state.task is None:
            self._close_state.task = asyncio.create_task(
                self._close_resources()
            )
            self._close_state.task.add_done_callback(
                partial(log_task_failure, operation="outbound_shutdown")
            )
        return self._close_state.task

    async def _close_resources(self) -> None:
        """Drain logical borrowers before closing profiles in reverse."""
        try:
            await self.pools.aclose()
        finally:
            try:
                await self._close_state.stack.aclose()
            finally:
                _log_shutdown_summary(self.pools.snapshots())


_RUNTIME_STATE: dict[str, OutboundRuntime | None] = {"runtime": None}

_CAPACITY_FIELDS: Final = {
    OutboundPoolName.LLM: "OUTBOUND_LLM_CONCURRENCY",
    OutboundPoolName.RETRIEVAL: "OUTBOUND_RETRIEVAL_CONCURRENCY",
    OutboundPoolName.RERANK: "OUTBOUND_RERANK_CONCURRENCY",
    OutboundPoolName.NL2SQL: "OUTBOUND_NL2SQL_CONCURRENCY",
    OutboundPoolName.ANALYSIS_CONTROL: "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
    OutboundPoolName.ANALYSIS_STATUS: "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
    OutboundPoolName.IAM: "OUTBOUND_IAM_CONCURRENCY",
    OutboundPoolName.SPA_FAQ: "OUTBOUND_SPA_FAQ_CONCURRENCY",
    OutboundPoolName.BI: "OUTBOUND_BI_CONCURRENCY",
    OutboundPoolName.OBS: "OUTBOUND_OBS_CONCURRENCY",
    OutboundPoolName.RELAY_CONTROL: "OUTBOUND_RELAY_CONTROL_CONCURRENCY",
    OutboundPoolName.INTEROP: "OUTBOUND_INTEROP_CONCURRENCY",
}


def _configured_capacities(
    config: ServerConfig,
) -> dict[OutboundPoolName, int]:
    """Project the twelve fixed configuration fields onto their enum keys."""
    return {
        name: getattr(config, field_name)
        for name, field_name in _CAPACITY_FIELDS.items()
    }


def _log_startup_summary(
    snapshots: tuple[OutboundPoolSnapshot, ...],
) -> None:
    """Log one deterministic startup summary using fixed safe values."""
    pools = ",".join(
        f"{snapshot.name.value}(capacity={snapshot.capacity})"
        for snapshot in snapshots
    )
    _LOGGER.info("outbound runtime startup pools=%s", pools)


def _log_shutdown_summary(
    snapshots: tuple[OutboundPoolSnapshot, ...],
) -> None:
    """Log final safe counters after resources have finished closing."""
    pools = ",".join(
        f"{snapshot.name.value}(capacity={snapshot.capacity},"
        f"in_use={snapshot.in_use},waiting={snapshot.waiting},"
        f"max_in_use={snapshot.max_in_use},started={snapshot.started},"
        f"completed={snapshot.completed},failed={snapshot.failed},"
        f"cancelled={snapshot.cancelled},"
        f"total_wait_ms={snapshot.total_wait_seconds * 1000:.3f},"
        f"max_wait_ms={snapshot.max_wait_seconds * 1000:.3f})"
        for snapshot in snapshots
    )
    _LOGGER.info("outbound runtime shutdown pools=%s", pools)


def _openai_endpoint(
    config: ServerConfig,
) -> tuple[str, str | None]:
    """Resolve the immutable direct-or-relay LLM endpoint at startup."""
    sensitive = get_sensitive_config()
    if relay_mode_enabled():
        return (
            sensitive.RELAY_API_KEY.get_secret_value(),
            f"{config.RELAY_BASE_URL}/v1/relay/llm",
        )
    return (
        sensitive.API_KEY.get_secret_value(),
        sensitive.BASE_URL or None,
    )


async def _close_openai_resource(
    client: AsyncOpenAI,
    http_client: httpx.AsyncClient,
) -> None:
    """Close the SDK owner, falling back to its dedicated HTTP client."""
    close = getattr(client, "close", None)
    if close is None:
        await http_client.aclose()
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _build_openai_client(
    config: ServerConfig,
    factories: OutboundResourceFactories,
) -> tuple[AsyncOpenAI, httpx.AsyncClient]:
    """Build one OpenAI client over its own persistent HTTPX transport."""
    api_key, base_url = _openai_endpoint(config)
    http_client = httpx.AsyncClient(
        verify=_resolve_verify(config),
        timeout=None,
        limits=httpx.Limits(
            max_connections=config.HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=config.HTTP_MAX_KEEPALIVE,
        ),
    )
    try:
        client = factories.openai(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=0,
        )
    except BaseException:
        # The HTTPX client is not registered until the SDK owns it.
        # Roll it back here so a failed startup cannot leak a socket pool.
        await http_client.aclose()
        raise
    return client, http_client


async def init_outbound_runtime(
    config: ServerConfig | None = None,
    *,
    factories: OutboundResourceFactories | None = None,
) -> OutboundRuntime:
    """Construct privately and publish only a fully initialized runtime."""
    if _RUNTIME_STATE["runtime"] is not None:
        raise OutboundRuntimeStateError(
            "outbound runtime is already initialized"
        )
    resolved = config if config is not None else ServerConfig()
    resolved_factories = factories or OutboundResourceFactories()
    stack = AsyncExitStack()
    pools = OutboundPoolRegistry(
        _configured_capacities(resolved),
        wait_warn_seconds=resolved.OUTBOUND_POOL_WAIT_WARN_SECONDS,
    )
    try:
        http = build_outbound_http_runtime(
            resolved,
            pools,
            stack,
            factories=resolved_factories.http,
        )
        openai, openai_http = await _build_openai_client(
            resolved,
            resolved_factories,
        )

        async def close_openai() -> None:
            """Close the SDK and its dedicated HTTPX owner exactly once."""
            await _close_openai_resource(openai, openai_http)

        # Register the SDK rollback before later resource constructors run.
        # OBS or Interop startup may fail after this point.
        stack.push_async_callback(close_openai)

        obs = build_obs_client_runtime(
            resolved,
            pools,
            factory=resolved_factories.obs,
        )
        if obs is not None:
            stack.push_async_callback(obs.aclose)

        if resolved_factories.interop is not None:
            interop = resolved_factories.interop(pools)
        else:
            # Load after the outbound package is fully initialized; Interop
            # imports the typed pool modules from this package.
            interop_module = importlib.import_module(
                "mcp_server_phytomni.interop.runtime"
            )
            interop = interop_module.build_interop_resource_runtime(pools)
        if interop is not None:
            stack.push_async_callback(interop.aclose)
    except BaseException:
        await stack.aclose()
        raise
    runtime = OutboundRuntime(
        pools=pools,
        http=http,
        openai=openai,
        obs=obs,
        interop=interop,
        _close_state=_OutboundRuntimeCloseState(stack),
    )
    _RUNTIME_STATE["runtime"] = runtime
    _log_startup_summary(pools.snapshots())
    return runtime


def current_outbound_runtime() -> OutboundRuntime:
    """Return the active runtime or fail before any transport invocation."""
    runtime = _RUNTIME_STATE["runtime"]
    if runtime is None:
        raise OutboundRuntimeStateError("outbound runtime is not initialized")
    if runtime.closing:
        raise OutboundRuntimeStateError("outbound runtime is closing")
    return runtime


def current_obs_runtime() -> ObsClientRuntime:
    """Return the active OBS runtime or fail before an OBS operation."""
    obs_runtime = current_outbound_runtime().obs
    if obs_runtime is None:
        raise RuntimeError("OBS runtime is unavailable")
    return obs_runtime


def current_outbound_http_client(
    pool: OutboundPoolName,
) -> BoundAsyncRequestClient:
    """Return one current HTTP client bound to a typed logical pool."""
    return current_outbound_runtime().http.for_pool(pool)


async def aclose_outbound_runtime() -> None:
    """Close the current runtime once and clear its slot on completion."""
    runtime = _RUNTIME_STATE["runtime"]
    try:
        await aclose_cleanup_runtime()
    finally:
        if runtime is not None:
            close_task = runtime.begin_close()

            def clear_slot(_task: asyncio.Task[None]) -> None:
                if _RUNTIME_STATE["runtime"] is runtime:
                    _RUNTIME_STATE["runtime"] = None

            close_task.add_done_callback(clear_slot)
            await asyncio.wait({close_task})
            close_task.result()
            clear_slot(close_task)


__all__ = [
    "OutboundResourceFactories",
    "OutboundRuntime",
    "OutboundRuntimeStateError",
    "aclose_outbound_runtime",
    "current_outbound_runtime",
    "init_outbound_runtime",
]
