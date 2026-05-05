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
from pathlib import Path
from typing import Any, cast

from .exceptions import CacheError
from .key_builder import KeyBuilder
from .lock import LockManager
from .serializer import dumps, loads
from .storage import Storage

logger = logging.getLogger(__name__)

_atexit_registered = set()
_CACHE_MISS = object()

DEFAULT_CACHE_DB_ENV = "PHYTOMNI_CACHE_DB"
DEFAULT_CACHE_DB_PATH = Path(".cache") / "phytomni" / "func_cache.sqlite"


def default_cache_db_path():
    """Return the default SQLite path for function result cache storage."""
    env_path = os.getenv(DEFAULT_CACHE_DB_ENV)
    if env_path:
        return env_path
    return str(DEFAULT_CACHE_DB_PATH)


def _resolve_db_path(db_path):
    """Resolve an explicit or environment-backed cache database path."""
    if db_path is not None:
        return os.fspath(db_path)
    return default_cache_db_path()


def func_cache(
    key_params=None,
    db_path=None,
    ttl=None,
    compress=False,
    lock_timeout=10,
    lock_expire=300,
    exclude_params=None,
):
    """Decorator that caches function results to SQLite storage.

    This decorator wraps a function to cache its results based on the
    function's arguments. Cache entries are stored in an SQLite database
    with optional TTL, compression, and distributed locking. Synchronous and
    asynchronous target functions are both supported.

    Args:
        key_params: List of parameter names to include in cache key. If None,
            all non-excluded parameters are used.
        db_path: Path to the SQLite database file. If None, the decorator uses
            PHYTOMNI_CACHE_DB or `.cache/phytomni/func_cache.sqlite`.
        ttl: Time-to-live in seconds for cache entries. If None, entries
            persist until explicitly cleared.
        compress: Whether to compress cached values using zlib.
        lock_timeout: Maximum seconds to wait for lock acquisition on cache
            miss.
        lock_expire: Seconds before a lock is considered expired and can be
            stolen.
        exclude_params: Parameters to omit from the key, such as secrets,
            clients, sessions, and LangGraph checkpointers.

    Returns:
        A decorator function that wraps the target function with caching.
    """

    def decorator(func):
        resolved_db_path = _resolve_db_path(db_path)
        storage = Storage.get_instance(resolved_db_path)
        kb = KeyBuilder(
            func,
            key_params=key_params,
            exclude_params=exclude_params,
        )
        lock_mgr = LockManager(storage, lock_timeout, lock_expire)

        db_abs = os.path.abspath(resolved_db_path)
        if db_abs not in _atexit_registered:
            atexit.register(storage.close)
            _atexit_registered.add(db_abs)

        _check_and_update_meta(storage, kb.func_id, kb.key_params, compress)

        hits = 0
        misses = 0

        def build_cache_key(args, kwargs):
            """Build a cache key or return None when caching is unsafe."""
            try:
                return kb.build_key(args, kwargs)
            except CacheError as e:
                logger.warning("Cache key build failed, falling back: %s", e)
                return None

        def delete_corrupted(cache_key):
            """Delete one corrupted cache entry, ignoring storage failures."""
            try:
                storage.delete_entry(kb.func_id, cache_key)
            except CacheError:
                pass

        def read_cached(cache_key):
            """Return cached value or a miss sentinel."""
            try:
                cached = storage.get(kb.func_id, cache_key)
            except CacheError as e:
                logger.warning("Cache read error, falling back: %s", e)
                return _CACHE_MISS

            if cached is not None:
                try:
                    return loads(cached, compress)
                except CacheError:
                    logger.warning(
                        "Cache deserialization failed, removing corrupted "
                        "entry: %s:%s",
                        kb.func_id,
                        cache_key,
                    )
                    delete_corrupted(cache_key)
            return _CACHE_MISS

        def write_cached(cache_key, result):
            """Serialize and store a cache value."""
            try:
                value = dumps(result, compress)
                storage.set(kb.func_id, cache_key, value, ttl)
            except CacheError as e:
                logger.warning("Cache write failed: %s", e)

        async def delete_corrupted_async(cache_key):
            """Delete one corrupted cache entry without blocking the loop."""
            try:
                await asyncio.to_thread(
                    storage.delete_entry,
                    kb.func_id,
                    cache_key,
                )
            except CacheError:
                pass

        async def read_cached_async(cache_key):
            """Return cached async value or a miss sentinel."""
            try:
                cached = await asyncio.to_thread(
                    storage.get,
                    kb.func_id,
                    cache_key,
                )
            except CacheError as e:
                logger.warning("Cache read error, falling back: %s", e)
                return _CACHE_MISS

            if cached is not None:
                try:
                    return loads(cached, compress)
                except CacheError:
                    logger.warning(
                        "Cache deserialization failed, removing corrupted "
                        "entry: %s:%s",
                        kb.func_id,
                        cache_key,
                    )
                    await delete_corrupted_async(cache_key)
            return _CACHE_MISS

        async def write_cached_async(cache_key, result):
            """Serialize and store an async cache value."""
            try:
                value = dumps(result, compress)
                await asyncio.to_thread(
                    storage.set,
                    kb.func_id,
                    cache_key,
                    value,
                    ttl,
                )
            except CacheError as e:
                logger.warning("Cache write failed: %s", e)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper for cache lookup, execution, and storage."""
            nonlocal hits, misses

            cache_key = build_cache_key(args, kwargs)
            if cache_key is None:
                return func(*args, **kwargs)

            result = read_cached(cache_key)
            if result is not _CACHE_MISS:
                hits += 1
                return result

            # ── Cache miss → Acquire lock ──
            locked = False
            try:
                lock_mgr.acquire(kb.func_id, cache_key)
                locked = True
            except CacheError as e:
                logger.warning("Failed to acquire lock, falling back: %s", e)
                misses += 1
                return func(*args, **kwargs)

            try:
                # ── Double-check read ──
                result = read_cached(cache_key)
                if result is not _CACHE_MISS:
                    hits += 1
                    return result

                # ── Execute original function ──
                misses += 1
                result = func(*args, **kwargs)

                # ── Write to cache ──
                write_cached(cache_key, result)

                return result
            finally:
                if locked:
                    try:
                        lock_mgr.release(kb.func_id, cache_key)
                    except Exception:
                        pass

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            """Async wrapper for cache lookup, execution, and storage."""
            nonlocal hits, misses

            cache_key = build_cache_key(args, kwargs)
            if cache_key is None:
                return await func(*args, **kwargs)

            result = await read_cached_async(cache_key)
            if result is not _CACHE_MISS:
                hits += 1
                return result

            owner = lock_mgr.owner(f"async:{id(asyncio.current_task())}")
            locked = False
            try:
                await asyncio.to_thread(
                    lock_mgr.acquire,
                    kb.func_id,
                    cache_key,
                    owner,
                )
                locked = True
            except CacheError as e:
                logger.warning("Failed to acquire lock, falling back: %s", e)
                misses += 1
                return await func(*args, **kwargs)

            try:
                result = await read_cached_async(cache_key)
                if result is not _CACHE_MISS:
                    hits += 1
                    return result

                misses += 1
                result = await func(*args, **kwargs)
                await write_cached_async(cache_key, result)
                return result
            finally:
                if locked:
                    try:
                        await asyncio.to_thread(
                            lock_mgr.release,
                            kb.func_id,
                            cache_key,
                            owner,
                        )
                    except Exception:
                        pass

        def cache_clear():
            """Clear all cache entries and locks for the decorated function."""
            try:
                storage.delete_func(kb.func_id)
                storage.cleanup_func_locks(kb.func_id)
            except CacheError as e:
                logger.warning("Failed to clear cache: %s", e)

        def cache_info():
            """Return cache statistics for the decorated function."""
            try:
                count = storage.count(kb.func_id)
            except CacheError:
                count = -1
            return {"hits": hits, "misses": misses, "count": count}

        target_wrapper = (
            async_wrapper if inspect.iscoroutinefunction(func) else wrapper
        )
        c_wrapper = cast(Any, target_wrapper)
        c_wrapper.cache_clear = cache_clear
        c_wrapper.cache_info = cache_info
        return c_wrapper

    return decorator


def _check_and_update_meta(storage, func_id, key_params, compress):
    """Check and update cache metadata, clearing cache on config changes."""
    try:
        meta = storage.get_meta(func_id)
        if meta is None:
            storage.set_meta(func_id, key_params, compress)
        else:
            old_params, old_compress = meta
            if old_params != key_params or old_compress != compress:
                changes = []
                if old_params != key_params:
                    changes.append(f"key_params: {old_params} → {key_params}")
                if old_compress != compress:
                    changes.append(f"compress: {old_compress} → {compress}")
                logger.warning(
                    "Detected cache config change for %s: %s, "
                    "automatically clearing old cache",
                    func_id,
                    "; ".join(changes),
                )
                storage.delete_func(func_id)
                storage.cleanup_func_locks(func_id)
                storage.set_meta(func_id, key_params, compress)
    except CacheError as e:
        logger.warning("Metadata check failed: %s", e)
