# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cache runtime: per-function cache orchestration.

Holds CacheRuntime (the sync+async cache dispatch class) and the
_CACHE_MISS sentinel. The public decorator, options, and lifecycle
helpers live in decorator.py and lifecycle.py respectively.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .exceptions import CacheError
from .key_builder import KeyBuilder
from .lifecycle import (
    _check_and_update_meta,
    _register_storage_close,
    _resolve_db_path,
)
from .lock import LockManager
from .serializer import dumps, loads
from .storage import Storage

__all__ = [
    "CacheRuntime",
    "CacheStats",
]

logger = logging.getLogger(__name__)

_CACHE_MISS = object()


def _log_warning(message: str) -> None:
    """Log a preformatted warning message."""
    logger.warning(message)


@dataclass
class CacheStats:
    """Mutable hit/miss counters for a cached function.

    Attributes:
        hits: Number of successful cache reads.
        misses: Number of cache misses that required function execution.
    """

    hits: int = 0
    misses: int = 0


class CacheRuntime:
    """Runtime state and operations for one decorated function.

    Attributes:
        func: Wrapped callable.
        options: Resolved cache options for the callable.
        storage: SQLite-backed cache storage.
        key_builder: Cache-key builder for the wrapped callable.
        lock_manager: Database-backed lock manager.
        stats: Mutable cache hit and miss counters.
    """

    def __init__(self, func: Any, options: Any):
        """Initialize cache storage, key builder, and lock manager."""
        self.func = func
        self.options = options
        db_path = _resolve_db_path(options.db_path)
        self.storage = Storage.get_instance(db_path)
        self.key_builder = KeyBuilder(
            func,
            key_params=options.key_params,
            exclude_params=options.exclude_params,
        )
        self.lock_manager = LockManager(
            self.storage,
            options.lock_timeout,
            options.lock_expire,
        )
        self.stats = CacheStats()
        self._async_locks: dict[tuple[int, str], asyncio.Lock] = {}
        _register_storage_close(db_path, self.storage)
        _check_and_update_meta(
            self.storage,
            self.key_builder.func_id,
            self.key_builder.key_params,
            options.compress,
        )

    def call(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """Return a cached sync result or execute the wrapped function.

        Args:
            args: Positional arguments passed to the wrapped function.
            kwargs: Keyword arguments passed to the wrapped function.

        Returns:
            Cached or newly computed function result.
        """
        cache_key = self.build_cache_key(args, kwargs)
        if cache_key is None:
            return self.func(*args, **kwargs)

        cached = self.read_cached(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        return self.compute_locked(cache_key, args, kwargs)

    async def call_async(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """Return a cached async result or execute the wrapped coroutine.

        Args:
            args: Positional arguments passed to the wrapped coroutine.
            kwargs: Keyword arguments passed to the wrapped coroutine.

        Returns:
            Cached or newly computed coroutine result.
        """
        cache_key = self.build_cache_key(args, kwargs)
        if cache_key is None:
            return await self.func(*args, **kwargs)

        cached = await self.read_cached_async(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        return await self.compute_locked_async(cache_key, args, kwargs)

    def build_cache_key(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """Build a cache key or return None when caching is unsafe.

        Args:
            args: Positional arguments for the wrapped callable.
            kwargs: Keyword arguments for the wrapped callable.

        Returns:
            Cache key string, or None when key construction fails.
        """
        try:
            return self.key_builder.build_key(args, kwargs)
        except CacheError as exc:
            _log_warning(f"Cache key build failed, falling back: {exc}")
            return None

    def read_cached(self, cache_key: str):
        """Return cached value or a miss sentinel.

        Args:
            cache_key: Cache key for the wrapped callable invocation.

        Returns:
            Cached value, or the internal cache-miss sentinel.
        """
        try:
            cached = self.storage.get(self.key_builder.func_id, cache_key)
        except CacheError as exc:
            _log_warning(f"Cache read error, falling back: {exc}")
            return _CACHE_MISS
        return self.deserialize_cached(cache_key, cached)

    async def read_cached_async(self, cache_key: str):
        """Return cached async value or a miss sentinel.

        Args:
            cache_key: Cache key for the wrapped coroutine invocation.

        Returns:
            Cached value, or the internal cache-miss sentinel.
        """
        try:
            cached = self.storage.get(self.key_builder.func_id, cache_key)
        except CacheError as exc:
            _log_warning(f"Cache read error, falling back: {exc}")
            return _CACHE_MISS
        return await self.deserialize_cached_async(cache_key, cached)

    def write_cached(self, cache_key: str, result: Any) -> None:
        """Serialize and store a cache value.

        Args:
            cache_key: Cache key for the stored result.
            result: Function result to serialize and store.

        Returns:
            None. Storage failures are logged and ignored.
        """
        try:
            value = dumps(result, self.options.compress)
            self.storage.set(
                self.key_builder.func_id,
                cache_key,
                value,
                self.options.ttl,
            )
        except CacheError as exc:
            _log_warning(f"Cache write failed: {exc}")

    async def write_cached_async(self, cache_key: str, result: Any) -> None:
        """Serialize and store an async cache value.

        Args:
            cache_key: Cache key for the stored result.
            result: Coroutine result to serialize and store.

        Returns:
            None. Storage failures are logged and ignored.
        """
        try:
            value = dumps(result, self.options.compress)
            self.storage.set(
                self.key_builder.func_id,
                cache_key,
                value,
                self.options.ttl,
            )
        except CacheError as exc:
            _log_warning(f"Cache write failed: {exc}")

    def compute_locked(
        self,
        cache_key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        """Compute a sync cache miss under a database lock.

        Args:
            cache_key: Cache key for the wrapped callable invocation.
            args: Positional arguments for the wrapped callable.
            kwargs: Keyword arguments for the wrapped callable.

        Returns:
            Cached value produced by another process, or newly computed
            value.
        """
        if not self.acquire_lock(cache_key):
            self.stats.misses += 1
            return self.func(*args, **kwargs)
        try:
            return self.compute_after_lock(cache_key, args, kwargs)
        finally:
            self.release_lock(cache_key)

    async def compute_locked_async(
        self,
        cache_key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        """Compute an async cache miss under a database lock.

        Args:
            cache_key: Cache key for the wrapped coroutine invocation.
            args: Positional arguments for the wrapped coroutine.
            kwargs: Keyword arguments for the wrapped coroutine.

        Returns:
            Cached value produced by another process, or newly computed
            value.
        """
        async with self.async_lock_for_key(cache_key):
            owner = self.lock_manager.owner(
                f"async:{id(asyncio.current_task())}"
            )
            if not await self.acquire_lock_async(cache_key, owner):
                self.stats.misses += 1
                return await self.func(*args, **kwargs)
            try:
                return await self.compute_after_lock_async(
                    cache_key, args, kwargs
                )
            finally:
                await self.release_lock_async(cache_key, owner)

    def async_lock_for_key(self, cache_key: str) -> asyncio.Lock:
        """Return the event-loop-local lock for one async cache key.

        Args:
            cache_key: Cache key requiring in-process async
                synchronization.

        Returns:
            Event-loop-local lock for the cache key.
        """
        loop_key = (id(asyncio.get_running_loop()), cache_key)
        lock = self._async_locks.get(loop_key)
        if lock is None:
            lock = asyncio.Lock()
            self._async_locks[loop_key] = lock
        return lock

    def compute_after_lock(
        self,
        cache_key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        """Double-check cache after lock, then compute and store.

        Args:
            cache_key: Cache key for the wrapped callable invocation.
            args: Positional arguments for the wrapped callable.
            kwargs: Keyword arguments for the wrapped callable.

        Returns:
            Cached value after lock acquisition, or newly computed value.
        """
        cached = self.read_cached(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        self.stats.misses += 1
        result = self.func(*args, **kwargs)
        self.write_cached(cache_key, result)
        return result

    async def compute_after_lock_async(
        self,
        cache_key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        """Double-check async cache after lock, then compute and store.

        Args:
            cache_key: Cache key for the wrapped coroutine invocation.
            args: Positional arguments for the wrapped coroutine.
            kwargs: Keyword arguments for the wrapped coroutine.

        Returns:
            Cached value after lock acquisition, or newly computed value.
        """
        cached = await self.read_cached_async(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        self.stats.misses += 1
        result = await self.func(*args, **kwargs)
        await self.write_cached_async(cache_key, result)
        return result

    def acquire_lock(self, cache_key: str) -> bool:
        """Acquire a sync lock, returning False on cache lock failures.

        Args:
            cache_key: Cache key to lock.

        Returns:
            True when the cache lock is acquired, otherwise False.
        """
        try:
            self.lock_manager.acquire(self.key_builder.func_id, cache_key)
            return True
        except CacheError as exc:
            _log_warning(f"Failed to acquire lock, falling back: {exc}")
            return False

    async def acquire_lock_async(self, cache_key: str, owner: str) -> bool:
        """Acquire an async lock, returning False on lock failures.

        Args:
            cache_key: Cache key to lock.
            owner: Explicit lock owner shared with release.

        Returns:
            True when the cache lock is acquired, otherwise False.
        """
        try:
            self.lock_manager.acquire(
                self.key_builder.func_id, cache_key, owner
            )
            return True
        except CacheError as exc:
            _log_warning(f"Failed to acquire lock, falling back: {exc}")
            return False

    def release_lock(self, cache_key: str) -> None:
        """Release a sync lock, ignoring cleanup failures.

        Args:
            cache_key: Cache key whose lock should be released.

        Returns:
            None. Cleanup failures are logged and ignored.
        """
        try:
            self.lock_manager.release(self.key_builder.func_id, cache_key)
        except CacheError as exc:
            _log_warning(f"Failed to release lock, ignoring: {exc}")

    async def release_lock_async(self, cache_key: str, owner: str) -> None:
        """Release an async lock, ignoring cleanup failures.

        Args:
            cache_key: Cache key whose lock should be released.
            owner: Explicit lock owner used during acquisition.

        Returns:
            None. Cleanup failures are logged and ignored.
        """
        try:
            self.lock_manager.release(
                self.key_builder.func_id, cache_key, owner
            )
        except CacheError as exc:
            _log_warning(f"Failed to release lock, ignoring: {exc}")

    def deserialize_cached(self, cache_key: str, cached: Any):
        """Deserialize cached bytes or remove a corrupted entry.

        Args:
            cache_key: Cache key associated with the cached bytes.
            cached: Raw cached bytes, or None for a cache miss.

        Returns:
            Deserialized value, or the internal cache-miss sentinel.
        """
        if cached is None:
            return _CACHE_MISS
        try:
            return loads(cached, self.options.compress)
        except CacheError:
            self._log_corrupted_entry(cache_key)
            self._delete_corrupted(cache_key)
            return _CACHE_MISS

    async def deserialize_cached_async(self, cache_key: str, cached: Any):
        """Deserialize async cached bytes or remove a corrupted entry.

        Args:
            cache_key: Cache key associated with the cached bytes.
            cached: Raw cached bytes, or None for a cache miss.

        Returns:
            Deserialized value, or the internal cache-miss sentinel.
        """
        if cached is None:
            return _CACHE_MISS
        try:
            return loads(cached, self.options.compress)
        except CacheError:
            self._log_corrupted_entry(cache_key)
            await self._delete_corrupted_async(cache_key)
            return _CACHE_MISS

    def _delete_corrupted(self, cache_key: str) -> None:
        """Delete one corrupted cache entry, ignoring storage failures."""
        try:
            self.storage.delete_entry(self.key_builder.func_id, cache_key)
        except CacheError:
            pass

    async def _delete_corrupted_async(self, cache_key: str) -> None:
        """Delete one corrupted cache entry, ignoring storage failures."""
        try:
            self.storage.delete_entry(self.key_builder.func_id, cache_key)
        except CacheError:
            pass

    def _log_corrupted_entry(self, cache_key: str) -> None:
        """Log a corrupted cache entry warning."""
        _log_warning(
            "Cache deserialization failed, removing corrupted entry: "
            f"{self.key_builder.func_id}:{cache_key}",
        )

    def cache_clear(self) -> None:
        """Clear all cache entries and locks for the decorated function."""
        try:
            self.storage.delete_func(self.key_builder.func_id)
            self.storage.cleanup_func_locks(self.key_builder.func_id)
        except CacheError as exc:
            _log_warning(f"Failed to clear cache: {exc}")

    def cache_info(self) -> dict[str, int]:
        """Return cache statistics for the decorated function.

        Returns:
            Dictionary with hit count, miss count, and current entry count.
        """
        try:
            count = self.storage.count(self.key_builder.func_id)
        except CacheError:
            count = -1
        return {
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "count": count,
        }
