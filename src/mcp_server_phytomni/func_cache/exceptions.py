"""Custom exception hierarchy for func_cache module."""


class CacheError(Exception):
    """Base exception for all cache-related errors."""


class CacheConfigError(CacheError):
    """Raised when cache configuration is invalid."""


class LockTimeout(CacheError):
    """Raised when lock acquisition times out."""


class SerializationError(CacheError):
    """Raised when serialization or deserialization fails."""


class StorageError(CacheError):
    """Raised when database storage operation fails."""
