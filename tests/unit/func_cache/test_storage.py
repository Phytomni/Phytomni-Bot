# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for SQLite-backed func_cache storage."""

import time

import pytest

from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit


@pytest.fixture(name="cache_storage")
def cache_storage_fixture(tmp_path):
    cache_storage = Storage(str(tmp_path / "func_cache.sqlite"))
    yield cache_storage
    cache_storage.close()


def test_storage_get_set_count_and_ttl_expiration(cache_storage):
    cache_storage.set("func", "key", b"value")

    assert cache_storage.get("func", "key") == b"value"
    assert cache_storage.count("func") == 1

    cache_storage.set("func", "key", b"expired", ttl=0)

    assert cache_storage.get("func", "key") is None
    assert cache_storage.count("func") == 0


def test_storage_metadata_round_trip(cache_storage):
    cache_storage.set_meta("func", ["alpha", "beta"], compress=True)

    assert cache_storage.get_meta("func") == (["alpha", "beta"], True)


def test_storage_lock_lifecycle(cache_storage):
    assert cache_storage.try_acquire_lock("func", "key", "owner-1", 60)
    assert not cache_storage.try_acquire_lock("func", "key", "owner-2", 60)

    cache_storage.release_lock("func", "key", "owner-1")

    assert cache_storage.try_acquire_lock("func", "key", "owner-2", 60)


def test_storage_can_replace_expired_lock(cache_storage):
    assert cache_storage.try_acquire_lock("func", "key", "stale-owner", 0.001)

    time.sleep(0.002)

    assert cache_storage.try_acquire_lock("func", "key", "fresh-owner", 0.001)
