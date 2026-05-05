# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for func_cache lock orchestration."""

import pytest

from mcp_server_phytomni.func_cache.exceptions import LockTimeout
from mcp_server_phytomni.func_cache.lock import LockManager

pytestmark = pytest.mark.unit


class GrantingStorage:
    """Fake storage that always grants lock acquisition."""

    def __init__(self):
        self.acquired_owner = None
        self.released_owner = None

    def try_acquire_lock(self, func_id, key_hash, owner, lock_expire):
        self.acquired_owner = owner
        return True

    def release_lock(self, func_id, key_hash, owner):
        self.released_owner = owner


class BlockingStorage:
    """Fake storage that never grants lock acquisition."""

    def __init__(self):
        self.calls = 0

    def try_acquire_lock(self, func_id, key_hash, owner, lock_expire):
        self.calls += 1
        return False


def test_lock_manager_releases_with_current_owner():
    storage = GrantingStorage()
    lock_manager = LockManager(storage, lock_timeout=0, lock_expire=300)

    lock_manager.acquire("func", "key")
    lock_manager.release("func", "key")

    assert storage.released_owner == storage.acquired_owner


def test_lock_manager_times_out_when_lock_is_not_granted():
    storage = BlockingStorage()
    lock_manager = LockManager(storage, lock_timeout=0, lock_expire=300)

    with pytest.raises(LockTimeout, match="Failed to acquire lock"):
        lock_manager.acquire("func", "key")

    assert storage.calls == 1
