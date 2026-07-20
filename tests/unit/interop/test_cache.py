# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for monotonic discovery TTL and per-target single-flight behavior."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_phytomni.interop.cache import (
    DiscoveryCache,
    get_or_create_discovery_cache,
)
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryError,
    DiscoveryResult,
    InteropCapability,
)
from mcp_server_phytomni.interop.models import MCPStdioTarget

pytestmark = pytest.mark.unit


class _Clock:
    """Manually advanced monotonic clock for deterministic TTL tests."""

    now = 10.0

    def __call__(self) -> float:
        """Return the current fake monotonic timestamp."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Advance the fake clock by a positive number of seconds."""
        self.now += seconds


def _result(target_id: str = "peer-cache") -> DiscoveryResult:
    """Build one cacheable metadata-only result."""
    return DiscoveryResult(
        data=(
            InteropCapability(
                target_id=target_id,
                kind="mcp",
                remote_name="lookup",
                qualified_name=f"{target_id}__lookup",
                description="metadata",
                input_schema={"type": "object", "properties": {}},
            ),
        )
    )


def test_cache_factory_reuses_one_cache_per_target() -> None:
    """The shared factory preserves injected per-target cache identity."""
    caches: dict[str, DiscoveryCache] = {}
    target = MCPStdioTarget.model_validate(
        {
            "id": "peer-cache",
            "kind": "mcp",
            "transport": "stdio",
            "command": "/opt/phytomni/bin/mcp-peer",
            "allowed_tools": ["lookup"],
            "discovery_ttl_seconds": 30,
        }
    )

    first = get_or_create_discovery_cache(
        caches,
        target,
        max_entries=4,
    )
    second = get_or_create_discovery_cache(
        caches,
        target,
        max_entries=1,
    )

    assert first is second
    assert caches == {"peer-cache": first}


async def test_success_is_cached_until_monotonic_expiry() -> None:
    """A clean result is reused, then rediscovered after its TTL."""
    clock = _Clock()
    cache = DiscoveryCache(ttl_seconds=5, clock=clock)
    calls = 0

    async def loader(_: str) -> DiscoveryResult:
        nonlocal calls
        calls += 1
        return _result()

    first = await cache.discover("peer-cache", loader)
    clock.advance(4.9)
    second = await cache.discover("peer-cache", loader)
    clock.advance(0.1)
    third = await cache.discover("peer-cache", loader)

    assert first is second
    assert third is not first
    assert calls == 2


async def test_concurrent_callers_share_one_target_flight() -> None:
    """Concurrent callers await one loader task and share its result."""
    cache = DiscoveryCache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def loader(_: str) -> DiscoveryResult:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _result()

    first_task = asyncio.create_task(cache.discover("peer-cache", loader))
    await started.wait()
    second_task = asyncio.create_task(cache.discover("peer-cache", loader))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first is second
    assert calls == 1


async def test_error_result_is_shared_but_not_negative_cached() -> None:
    """Waiters share one failure, while the next request retries discovery."""
    cache = DiscoveryCache()
    calls = 0

    async def loader(_: str) -> DiscoveryResult:
        nonlocal calls
        calls += 1
        return DiscoveryResult(
            errors=(
                DiscoveryError(
                    target_id="peer-http", kind="mcp", code="discovery_failed"
                ),
            )
        )

    first, second = await asyncio.gather(
        cache.discover("peer-cache", loader),
        cache.discover("peer-cache", loader),
    )
    third = await cache.discover("peer-cache", loader)

    assert first is second
    assert first.errors
    assert third is not first
    assert calls == 2


async def test_unexpected_loader_error_is_redacted_and_retried() -> None:
    """Raw loader exception text is replaced by the shared error shape."""
    cache = DiscoveryCache()
    calls = 0

    async def loader(_: str) -> DiscoveryResult:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret peer payload")

    first, second = await asyncio.gather(
        cache.discover("peer-cache", loader),
        cache.discover("peer-cache", loader),
    )
    third = await cache.discover("peer-cache", loader)

    assert first is second
    assert first.errors[0].code == "discovery_failed"
    assert "secret peer payload" not in repr(first)
    assert third is not first
    assert calls == 2


async def test_cache_evicts_oldest_successful_entry_at_capacity() -> None:
    """A bounded cache evicts insertion-oldest metadata and retries it."""
    cache = DiscoveryCache(max_entries=2)
    calls: dict[str, int] = {}

    async def loader(target_id: str) -> DiscoveryResult:
        calls[target_id] = calls.get(target_id, 0) + 1
        return _result(target_id)

    await cache.discover("peer-a", loader)
    await cache.discover("peer-b", loader)
    await cache.discover("peer-c", loader)
    await cache.discover("peer-b", loader)
    await cache.discover("peer-a", loader)

    assert calls == {"peer-a": 2, "peer-b": 1, "peer-c": 1}


def test_invalid_ttl_and_clear_are_deterministic() -> None:
    """TTL validation and explicit invalidation do not accept unsafe keys."""
    with pytest.raises(ValueError, match="positive"):
        DiscoveryCache(ttl_seconds=0)
    with pytest.raises(ValueError, match="max_entries"):
        DiscoveryCache(max_entries=0)
    with pytest.raises(ValueError, match="target_id"):
        DiscoveryCache().clear("")
