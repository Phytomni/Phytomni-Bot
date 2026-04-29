class CacheError(Exception):
    pass


class CacheConfigError(CacheError):
    pass


class LockTimeout(CacheError):
    pass


class SerializationError(CacheError):
    pass


class StorageError(CacheError):
    pass
