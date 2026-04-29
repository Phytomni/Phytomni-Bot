import os
import threading
import time

from .exceptions import LockTimeout, StorageError


class LockManager:

    def __init__(self, storage, lock_timeout=10, lock_expire=300):
        self.storage = storage
        self.lock_timeout = lock_timeout
        self.lock_expire = lock_expire

    def _owner(self):
        return f"{os.getpid()}:{threading.get_ident()}"

    def acquire(self, func_id, key_hash):
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
        owner = self._owner()
        self.storage.release_lock(func_id, key_hash, owner)
