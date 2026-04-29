# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Caching decorator for function result memoization with SQLite persistence."""

import functools
import logging
import atexit
import os
from typing import Any, cast

from .key_builder import KeyBuilder
from .serializer import dumps, loads
from .storage import Storage
from .lock import LockManager
from .exceptions import CacheError

logger = logging.getLogger(__name__)

_atexit_registered = set()


def func_cache(
    key_params=None,
    db_path=".func_cache.db",
    ttl=None,
    compress=False,
    lock_timeout=10,
    lock_expire=300,
):
    """Decorator that caches function results to SQLite storage.

    This decorator wraps a function to cache its results based on the
    function's arguments. Cache entries are stored in an SQLite database
    with optional TTL, compression, and distributed locking.

    Args:
        key_params: List of parameter names to include in cache key. If None,
            all parameters are used.
        db_path: Path to the SQLite database file.
        ttl: Time-to-live in seconds for cache entries. If None, entries
            persist until explicitly cleared.
        compress: Whether to compress cached values using zlib.
        lock_timeout: Maximum seconds to wait for lock acquisition on cache
            miss.
        lock_expire: Seconds before a lock is considered expired and can be
            stolen.

    Returns:
        A decorator function that wraps the target function with caching.
    """

    def decorator(func):
        storage = Storage.get_instance(db_path)
        kb = KeyBuilder(func, key_params)
        lock_mgr = LockManager(storage, lock_timeout, lock_expire)

        db_abs = os.path.abspath(db_path)
        if db_abs not in _atexit_registered:
            atexit.register(storage.close)
            _atexit_registered.add(db_abs)

        _check_and_update_meta(storage, kb.func_id, kb.key_params, compress)

        hits = 0
        misses = 0

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """Wrapper function that handles cache lookup, execution, and storage."""
            nonlocal hits, misses

            # ── Build key ──
            try:
                cache_key = kb.build_key(args, kwargs)
            except CacheError as e:
                logger.warning(f"Cache key build failed, falling back: {e}")
                return func(*args, **kwargs)

            # ── Lock-free read ──
            try:
                cached = storage.get(kb.func_id, cache_key)
            except CacheError as e:
                logger.warning(f"Cache read error, falling back: {e}")
                return func(*args, **kwargs)

            if cached is not None:
                try:
                    result = loads(cached, compress)
                    hits += 1
                    return result
                except CacheError:
                    logger.warning(
                        "Cache deserialization failed, "
                        f"removing corrupted entry: {kb.func_id}:{cache_key}"
                    )
                    try:
                        storage.delete_entry(kb.func_id, cache_key)
                    except CacheError:
                        pass

            # ── Cache miss → Acquire lock ──
            locked = False
            try:
                lock_mgr.acquire(kb.func_id, cache_key)
                locked = True
            except CacheError as e:
                logger.warning(f"Failed to acquire lock, falling back: {e}")
                misses += 1
                return func(*args, **kwargs)

            try:
                # ── Double-check read ──
                try:
                    cached = storage.get(kb.func_id, cache_key)
                    if cached is not None:
                        try:
                            result = loads(cached, compress)
                            hits += 1
                            return result
                        except CacheError:
                            try:
                                storage.delete_entry(kb.func_id, cache_key)
                            except CacheError:
                                pass
                except CacheError:
                    pass

                # ── Execute original function ──
                misses += 1
                result = func(*args, **kwargs)

                # ── Write to cache ──
                try:
                    value = dumps(result, compress)
                    storage.set(kb.func_id, cache_key, value, ttl)
                except CacheError as e:
                    logger.warning(f"Cache write failed: {e}")

                return result
            finally:
                if locked:
                    try:
                        lock_mgr.release(kb.func_id, cache_key)
                    except Exception:
                        pass

        def cache_clear():
            """Clear all cache entries and locks for the decorated function."""
            try:
                storage.delete_func(kb.func_id)
                storage.cleanup_func_locks(kb.func_id)
            except CacheError as e:
                logger.warning(f"Failed to clear cache: {e}")

        def cache_info():
            """Return cache statistics for the decorated function."""
            try:
                count = storage.count(kb.func_id)
            except CacheError:
                count = -1
            return {"hits": hits, "misses": misses, "count": count}

        c_wrapper = cast(Any, wrapper)
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
                    f"Detected cache config change for {func_id}: "
                    f"{'; '.join(changes)}, "
                    "automatically clearing old cache"
                )
                storage.delete_func(func_id)
                storage.cleanup_func_locks(func_id)
                storage.set_meta(func_id, key_params, compress)
    except CacheError as e:
        logger.warning(f"Metadata check failed: {e}")
