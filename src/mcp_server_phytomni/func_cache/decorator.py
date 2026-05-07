# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Caching decorator for function result memoization."""

import asyncio
import atexit
import functools
import inspect
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .exceptions import CacheError
from .key_builder import KeyBuilder
from .lock import LockManager
from .serializer import dumps, loads
from .storage import Storage

logger = logging.getLogger(__name__)


def _log_warning(message: str) -> None:
    """Log a preformatted warning message."""
    logger.warning(message)


_atexit_registered: set[str] = set()
_CACHE_MISS = object()

DEFAULT_CACHE_DB_ENV = "PHYTOMNI_CACHE_DB"
DEFAULT_CACHE_DB_PATH = Path(".cache") / "phytomni" / "func_cache.sqlite"


@dataclass(frozen=True)
class CacheOptions:
    """Resolved options for one cached function."""

    key_params: Any = None
    db_path: Any = None
    ttl: Any = None
    compress: bool = False
    lock_timeout: int = 10
    lock_expire: int = 300
    exclude_params: Any = None

    @classmethod
    def from_kwargs(cls, key_params: Any, kwargs: dict[str, Any]):
        """Build options while preserving keyword compatibility."""
        allowed = {
            "db_path",
            "ttl",
            "compress",
            "lock_timeout",
            "lock_expire",
            "exclude_params",
        }
        unknown = sorted(set(kwargs) - allowed)
        if unknown:
            joined = ", ".join(unknown)
            raise TypeError(f"Unknown func_cache option(s): {joined}")
        return cls(key_params=key_params, **kwargs)


@dataclass
class CacheStats:
    """Mutable hit/miss counters for a cached function."""

    hits: int = 0
    misses: int = 0


class CacheRuntime:
    """Runtime state and operations for one decorated function."""

    def __init__(self, func: Any, options: CacheOptions):
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
        """Return a cached sync result or execute the wrapped function."""
        cache_key = self.build_cache_key(args, kwargs)
        if cache_key is None:
            return self.func(*args, **kwargs)

        cached = self.read_cached(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        return self.compute_locked(cache_key, args, kwargs)

    async def call_async(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """Return a cached async result or execute the wrapped coroutine."""
        cache_key = self.build_cache_key(args, kwargs)
        if cache_key is None:
            return await self.func(*args, **kwargs)

        cached = await self.read_cached_async(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        return await self.compute_locked_async(cache_key, args, kwargs)

    def build_cache_key(self, args: tuple[Any, ...], kwargs: dict[str, Any]):
        """Build a cache key or return None when caching is unsafe."""
        try:
            return self.key_builder.build_key(args, kwargs)
        except CacheError as exc:
            _log_warning(f"Cache key build failed, falling back: {exc}")
            return None

    def read_cached(self, cache_key: str):
        """Return cached value or a miss sentinel."""
        try:
            cached = self.storage.get(self.key_builder.func_id, cache_key)
        except CacheError as exc:
            _log_warning(f"Cache read error, falling back: {exc}")
            return _CACHE_MISS
        return self.deserialize_cached(cache_key, cached)

    async def read_cached_async(self, cache_key: str):
        """Return cached async value or a miss sentinel."""
        try:
            cached = self.storage.get(self.key_builder.func_id, cache_key)
        except CacheError as exc:
            _log_warning(f"Cache read error, falling back: {exc}")
            return _CACHE_MISS
        return await self.deserialize_cached_async(cache_key, cached)

    def write_cached(self, cache_key: str, result: Any) -> None:
        """Serialize and store a cache value."""
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
        """Serialize and store an async cache value."""
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
        """Compute a sync cache miss under a database lock."""
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
        """Compute an async cache miss under a database lock."""
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
        """Return the event-loop-local lock for one async cache key."""
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
        """Double-check cache after lock, then compute and store."""
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
        """Double-check async cache after lock, then compute and store."""
        cached = await self.read_cached_async(cache_key)
        if cached is not _CACHE_MISS:
            self.stats.hits += 1
            return cached

        self.stats.misses += 1
        result = await self.func(*args, **kwargs)
        await self.write_cached_async(cache_key, result)
        return result

    def acquire_lock(self, cache_key: str) -> bool:
        """Acquire a sync lock, returning False on cache lock failures."""
        try:
            self.lock_manager.acquire(self.key_builder.func_id, cache_key)
            return True
        except CacheError as exc:
            _log_warning(f"Failed to acquire lock, falling back: {exc}")
            return False

    async def acquire_lock_async(self, cache_key: str, owner: str) -> bool:
        """Acquire an async lock, returning False on cache lock failures."""
        try:
            self.lock_manager.acquire(
                self.key_builder.func_id, cache_key, owner
            )
            return True
        except CacheError as exc:
            _log_warning(f"Failed to acquire lock, falling back: {exc}")
            return False

    def release_lock(self, cache_key: str) -> None:
        """Release a sync lock, ignoring cleanup failures."""
        try:
            self.lock_manager.release(self.key_builder.func_id, cache_key)
        except CacheError:
            pass

    async def release_lock_async(self, cache_key: str, owner: str) -> None:
        """Release an async lock, ignoring cleanup failures."""
        try:
            self.lock_manager.release(
                self.key_builder.func_id, cache_key, owner
            )
        except CacheError:
            pass

    def deserialize_cached(self, cache_key: str, cached: Any):
        """Deserialize cached bytes or remove a corrupted entry."""
        if cached is None:
            return _CACHE_MISS
        try:
            return loads(cached, self.options.compress)
        except CacheError:
            self._log_corrupted_entry(cache_key)
            self._delete_corrupted(cache_key)
            return _CACHE_MISS

    async def deserialize_cached_async(self, cache_key: str, cached: Any):
        """Deserialize async cached bytes or remove a corrupted entry."""
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
        """Return cache statistics for the decorated function."""
        try:
            count = self.storage.count(self.key_builder.func_id)
        except CacheError:
            count = -1
        return {
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "count": count,
        }


def default_cache_db_path():
    """Return the default SQLite path for function result cache storage."""
    env_path = os.getenv(DEFAULT_CACHE_DB_ENV)
    if env_path:
        return env_path
    return str(DEFAULT_CACHE_DB_PATH)


def func_cache(key_params=None, **kwargs):
    """Decorator that caches function results to SQLite storage.

    Args:
        key_params: List of parameter names to include in cache key. If None,
            all non-excluded parameters are used.
        **kwargs: Keyword-compatible cache options: db_path, ttl, compress,
            lock_timeout, lock_expire, and exclude_params.

    Returns:
        A decorator function that wraps the target function with caching.
    """
    options = CacheOptions.from_kwargs(key_params, kwargs)

    def decorator(func):
        runtime = CacheRuntime(func, options)

        @functools.wraps(func)
        def wrapper(*args, **wrapper_kwargs):
            """Wrapper for sync cache lookup, execution, and storage."""
            return runtime.call(args, wrapper_kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **wrapper_kwargs):
            """Wrapper for async cache lookup, execution, and storage."""
            return await runtime.call_async(args, wrapper_kwargs)

        target_wrapper = (
            async_wrapper if inspect.iscoroutinefunction(func) else wrapper
        )
        c_wrapper = cast(Any, target_wrapper)
        c_wrapper.cache_clear = runtime.cache_clear
        c_wrapper.cache_info = runtime.cache_info
        return c_wrapper

    return decorator


def _resolve_db_path(db_path):
    """Resolve an explicit or environment-backed cache database path."""
    if db_path is not None:
        return os.fspath(db_path)
    return default_cache_db_path()


def _register_storage_close(db_path: str, storage: Storage) -> None:
    """Register one storage close hook per absolute database path."""
    db_abs = str(Path(db_path).resolve())
    if db_abs in _atexit_registered:
        return
    atexit.register(storage.close)
    _atexit_registered.add(db_abs)


def _check_and_update_meta(storage, func_id, key_params, compress):
    """Check and update cache metadata, clearing cache on config changes."""
    try:
        meta = storage.get_meta(func_id)
        if meta is None:
            storage.set_meta(func_id, key_params, compress)
            return

        old_params, old_compress = meta
        if old_params == key_params and old_compress == compress:
            return

        _clear_changed_cache(
            storage,
            func_id,
            _cache_config_changes(
                old_params,
                key_params,
                old_compress,
                compress,
            ),
            key_params,
            compress,
        )
    except CacheError as exc:
        _log_warning(f"Metadata check failed: {exc}")


def _cache_config_changes(
    old_params,
    key_params,
    old_compress,
    compress,
) -> list[str]:
    """Return readable cache metadata changes."""
    changes = []
    if old_params != key_params:
        changes.append(f"key_params: {old_params} -> {key_params}")
    if old_compress != compress:
        changes.append(f"compress: {old_compress} -> {compress}")
    return changes


def _clear_changed_cache(
    storage,
    func_id,
    changes: list[str],
    key_params,
    compress,
) -> None:
    """Clear cache entries after metadata changes and persist new metadata."""
    changes_text = "; ".join(changes)
    _log_warning(
        f"Detected cache config change for {func_id}: {changes_text}, "
        "automatically clearing old cache",
    )
    storage.delete_func(func_id)
    storage.cleanup_func_locks(func_id)
    storage.set_meta(func_id, key_params, compress)
