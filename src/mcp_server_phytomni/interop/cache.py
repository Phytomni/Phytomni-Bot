# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded in-process caching for external capability discovery.

Only :class:`~interop.capabilities.DiscoveryResult` values are retained.  The
temporary LangChain tools and their transport closures live solely inside the
loader task, so cache entries cannot accidentally retain credentials or open
connections.  A per-target task map coalesces concurrent discovery calls.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .capabilities import DiscoveryError, DiscoveryResult

DiscoveryLoader = Callable[[str], Awaitable[DiscoveryResult]]
DEFAULT_MAX_ENTRIES = 256


class _LoaderFailureError(Exception):
    """Internal marker for a loader failure with no retained peer detail."""


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """One successful result and its monotonic expiry deadline."""

    expires_at: float
    result: DiscoveryResult


class DiscoveryCache:
    """Cache successful discovery results and single-flight each target."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty cache with bounded entries and a positive TTL."""
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a finite positive number")
        if (
            not isinstance(max_entries, int)
            or isinstance(max_entries, bool)
            or max_entries <= 0
        ):
            raise ValueError("max_entries must be a positive integer")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, _CacheEntry] = {}
        self._inflight: dict[str, asyncio.Task[DiscoveryResult]] = {}
        self._lock = asyncio.Lock()

    async def discover(
        self,
        target_id: str,
        loader: DiscoveryLoader,
        *,
        kind: str = "mcp",
    ) -> DiscoveryResult:
        """Return cached metadata or coalesce one target's discovery call.

        Results containing any errors are returned to all current waiters but
        are deliberately not cached.  Unexpected loader exceptions are turned
        into the same sanitized result shape, avoiding peer-derived exception
        text at callers that use this generic cache directly.
        """
        _validate_key(target_id)
        if not callable(loader):
            raise TypeError("loader must be callable")
        async with self._lock:
            now = self._clock()
            entry = self._entries.get(target_id)
            if entry is not None:
                if entry.expires_at > now:
                    return entry.result
                self._entries.pop(target_id, None)
            task = self._inflight.get(target_id)
            if task is None:
                task = asyncio.create_task(
                    self._run_loader(target_id, loader, kind)
                )
                self._inflight[target_id] = task
        return await asyncio.shield(task)

    async def get_or_discover(
        self,
        target_id: str,
        loader: DiscoveryLoader,
        *,
        kind: str = "mcp",
    ) -> DiscoveryResult:
        """Compatibility alias for :meth:`discover`."""
        return await self.discover(target_id, loader, kind=kind)

    def clear(self, target_id: str | None = None) -> None:
        """Drop cached entries without cancelling an in-flight discovery."""
        if target_id is None:
            self._entries.clear()
            return
        _validate_key(target_id)
        self._entries.pop(target_id, None)

    invalidate = clear

    async def _run_loader(
        self,
        target_id: str,
        loader: DiscoveryLoader,
        kind: str,
    ) -> DiscoveryResult:
        """Run one loader, cache a clean result, and release the flight."""
        try:
            try:
                result = await _call_loader(loader, target_id)
            except _LoaderFailureError:
                result = DiscoveryResult(
                    errors=(
                        DiscoveryError(
                            target_id=target_id,
                            kind=kind,
                            code="discovery_failed",
                        ),
                    )
                )
            if not result.errors:
                async with self._lock:
                    if (
                        target_id not in self._entries
                        and len(self._entries) >= self._max_entries
                    ):
                        oldest_target_id = next(iter(self._entries))
                        self._entries.pop(oldest_target_id, None)
                    self._entries[target_id] = _CacheEntry(
                        expires_at=self._clock() + self._ttl_seconds,
                        result=result,
                    )
            return result
        finally:
            async with self._lock:
                current = self._inflight.get(target_id)
                if current is asyncio.current_task():
                    self._inflight.pop(target_id, None)


async def _call_loader(
    loader: DiscoveryLoader, target_id: str
) -> DiscoveryResult:
    """Invoke a loader while discarding all exception detail."""
    try:
        result = await loader(target_id)
        if not isinstance(result, DiscoveryResult):
            raise TypeError("loader must return DiscoveryResult")
    except Exception:
        raise _LoaderFailureError from None
    return result


def _validate_key(target_id: Any) -> None:
    """Reject non-string cache keys before they can enter shared state."""
    if not isinstance(target_id, str) or not target_id:
        raise ValueError("target_id must be a non-empty string")


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DiscoveryCache",
    "DiscoveryLoader",
]
