"""Function result caching decorator with SQLite persistence.

This module provides a caching decorator that stores function results in an
SQLite database, supporting TTL, compression, and distributed locking for
concurrent access control.

Key Features:
    - TTL-based cache expiration
    - Optional zlib compression for large results
    - Distributed lock management to prevent cache stampedes
    - Per-function cache isolation via unique func_id
    - Automatic cleanup of expired entries and stale locks

Usage:
    @func_cache(ttl=3600)
    def expensive_function(arg1, arg2):
        ...

    # Access cache info
    expensive_function.cache_info()
    expensive_function.cache_clear()
"""

from .decorator import func_cache
from .storage import Storage

__all__ = ["func_cache", "Storage"]
