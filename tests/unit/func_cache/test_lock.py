# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for func_cache lock orchestration."""

import pytest

from mcp_server_phytomni.func_cache.exceptions import LockTimeoutError
from mcp_server_phytomni.func_cache.lock import LockManager

pytestmark = pytest.mark.unit


class GrantingStorage:
    """Fake storage that always grants lock acquisition."""

    def __init__(self):
        """Verify init  ."""
        self.acquired_owner = None
        self.released_owner = None
        self.acquired_key = None
        self.released_key = None
        self.lock_expire = None

    def try_acquire_lock(self, func_id, key_hash, owner, lock_expire):
        """Verify try acquire lock."""
        self.acquired_key = (func_id, key_hash)
        self.acquired_owner = owner
        self.lock_expire = lock_expire
        return True

    def release_lock(self, func_id, key_hash, owner):
        """Verify release lock."""
        self.released_key = (func_id, key_hash)
        self.released_owner = owner


class BlockingStorage:
    """Fake storage that never grants lock acquisition."""

    def __init__(self):
        """Verify init  ."""
        self.calls = 0
        self.last_attempt = None
        self.last_release = None

    def try_acquire_lock(self, func_id, key_hash, owner, lock_expire):
        """Verify try acquire lock."""
        self.calls += 1
        self.last_attempt = (func_id, key_hash, owner, lock_expire)
        return False

    def release_lock(self, func_id, key_hash, owner):
        """Record unexpected release attempts."""
        self.last_release = (func_id, key_hash, owner)


def test_lock_manager_releases_with_current_owner():
    """Verify lock manager releases with current owner."""
    storage = GrantingStorage()
    lock_manager = LockManager(storage, lock_timeout=0, lock_expire=300)

    lock_manager.acquire("func", "key")
    lock_manager.release("func", "key")

    assert storage.released_owner == storage.acquired_owner
    assert storage.released_key == storage.acquired_key
    assert storage.lock_expire == 300


def test_lock_manager_times_out_when_lock_is_not_granted():
    """Verify lock manager times out when lock is not granted."""
    storage = BlockingStorage()
    lock_manager = LockManager(storage, lock_timeout=0, lock_expire=300)

    with pytest.raises(LockTimeoutError, match="Failed to acquire lock"):
        lock_manager.acquire("func", "key")

    assert storage.calls == 1
    assert storage.last_attempt is not None
    func_id, key_hash, owner, lock_expire = storage.last_attempt
    assert (func_id, key_hash, lock_expire) == ("func", "key", 300)
    assert owner
