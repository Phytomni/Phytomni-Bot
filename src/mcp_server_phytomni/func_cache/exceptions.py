# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Custom exception hierarchy for func_cache module.

This module exposes `CacheError` and specialized cache configuration,
locking, serialization, and storage exceptions used by the cache package.
"""


class CacheError(Exception):
    """Base exception for all cache-related errors."""


class CacheConfigError(CacheError):
    """Raised when cache configuration is invalid."""


class LockTimeoutError(CacheError):
    """Raised when lock acquisition times out."""


class SerializationError(CacheError):
    """Raised when serialization or deserialization fails."""


class StorageError(CacheError):
    """Raised when database storage operation fails."""
