# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Caching decorator facade: public API for function result memoization.

Re-exports CacheOptions, CacheStats, and CacheRuntime from core.py,
and default_cache_db_path from lifecycle.py, keeping the public
import surface stable for existing consumers.
"""

import functools
import inspect
from dataclasses import dataclass
from typing import Any, cast

from .core import CacheRuntime, CacheStats
from .lifecycle import default_cache_db_path

__all__ = [
    "CacheOptions",
    "CacheRuntime",
    "CacheStats",
    "LONG_TTL_SECONDS",
    "default_cache_db_path",
    "func_cache",
]

# Shared TTL for caches over scarce remote resources (LLM completions,
# retrieval, rerank, nl2sql, BI lookups). Storage is ample but remote
# LLM/GPU concurrency is the bottleneck, so these entries persist ~90
# days; per-entry expiry is rewritable via the phytomni-cache CLI.
LONG_TTL_SECONDS = 90 * 24 * 3600


@dataclass(frozen=True)
class CacheOptions:
    """Resolved options for one cached function.

    Attributes:
        key_params: Parameter names included in cache keys.
        db_path: Optional SQLite cache database path.
        ttl: Optional cache entry time-to-live in seconds.
        compress: Whether serialized values use zlib compression.
        lock_timeout: Maximum seconds to wait for cache locks.
        lock_expire: Seconds before cache locks are considered stale.
        exclude_params: Parameter names excluded from cache keys.
    """

    key_params: Any = None
    db_path: Any = None
    ttl: Any = None
    compress: bool = False
    lock_timeout: int = 10
    lock_expire: int = 300
    exclude_params: Any = None

    @classmethod
    def from_kwargs(cls, key_params: Any, kwargs: dict[str, Any]):
        """Build options while preserving keyword compatibility.

        Args:
            key_params: Explicit parameter names to include in cache
                keys.
            kwargs: Keyword-compatible cache options.

        Returns:
            Resolved cache options.

        Raises:
            TypeError: If unknown cache options are supplied.
        """
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


def func_cache(key_params=None, **kwargs):
    """Decorator that caches function results to SQLite storage.

    Args:
        key_params: List of parameter names to include in cache key.
            If None, all non-excluded parameters are used.
        **kwargs: Keyword-compatible cache options: db_path, ttl,
            compress, lock_timeout, lock_expire, and exclude_params.

    Returns:
        A decorator function that wraps the target function with
        caching.
    """
    options = CacheOptions.from_kwargs(key_params, kwargs)

    def decorator(func):
        """Wrap one callable with the configured cache runtime.

        Args:
            func: Callable or coroutine function to cache.

        Returns:
            Wrapped callable with cache helpers attached.
        """
        runtime = CacheRuntime(func, options)

        @functools.wraps(func)
        def wrapper(*args, **wrapper_kwargs):
            """Wrapper for sync cache lookup, execution, and storage.

            Args:
                *args: Positional arguments forwarded to the wrapped
                    callable.
                **wrapper_kwargs: Keyword arguments forwarded to the
                    wrapped callable.

            Returns:
                Cached or newly computed callable result.
            """
            return runtime.call(args, wrapper_kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **wrapper_kwargs):
            """Wrapper for async cache lookup, execution, and storage.

            Args:
                *args: Positional arguments forwarded to the wrapped
                    coroutine.
                **wrapper_kwargs: Keyword arguments forwarded to the
                    wrapped coroutine.

            Returns:
                Cached or newly computed coroutine result.
            """
            return await runtime.call_async(args, wrapper_kwargs)

        target_wrapper = (
            async_wrapper if inspect.iscoroutinefunction(func) else wrapper
        )
        c_wrapper = cast(Any, target_wrapper)
        c_wrapper.cache_clear = runtime.cache_clear
        c_wrapper.cache_info = runtime.cache_info
        return c_wrapper

    return decorator
