# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Distributed lock manager using database-based locking."""

import os
import threading
import time

from .exceptions import LockTimeout, StorageError


class LockManager:
    """Manages distributed locks for cache entries.

    This class provides lock acquisition and release operations using a
    storage backend, with configurable timeout and expiration. It generates
    unique owners based on process and thread IDs.

    Instance Attributes:
        storage: Storage instance for lock operations.
        lock_timeout: Maximum seconds to wait for lock acquisition.
        lock_expire: Seconds before a lock is considered expired.
    """

    def __init__(self, storage, lock_timeout=10, lock_expire=300):
        """Initialize the lock manager.

        Args:
            storage: Storage instance for lock operations.
            lock_timeout: Maximum seconds to wait for lock acquisition.
            lock_expire: Seconds before a lock is considered expired.
        """
        self.storage = storage
        self.lock_timeout = lock_timeout
        self.lock_expire = lock_expire

    def _owner(self):
        """Generate a unique owner ID for the current process/thread."""
        pid = os.getpid()
        tid = threading.get_ident()
        return f"{pid}:{tid}"

    def acquire(self, func_id, key_hash):
        """Acquire a lock for the given cache entry.

        Blocks until the lock is acquired or timeout is reached.

        Args:
            func_id: The function identifier.
            key_hash: The cache key hash.

        Raises:
            LockTimeout: If lock cannot be acquired within timeout.
        """
        owner = self._owner()
        deadline = time.time() + self.lock_timeout

        while True:
            try:
                if self.storage.try_acquire_lock(
                    func_id, key_hash, owner, self.lock_expire
                ):
                    return
            except StorageError:
                pass

            if time.time() >= deadline:
                raise LockTimeout(
                    f"Failed to acquire lock ({self.lock_timeout}s): "
                    f"{func_id}:{key_hash}"
                )
            time.sleep(0.05)

    def release(self, func_id, key_hash):
        """Release the lock for the given cache entry.

        Args:
            func_id: The function identifier.
            key_hash: The cache key hash.
        """
        owner = self._owner()
        self.storage.release_lock(func_id, key_hash, owner)
