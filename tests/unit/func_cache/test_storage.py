# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for SQLite-backed func_cache storage.

Covers value storage, TTL expiration, metadata round trips, lock lifecycle,
and replacement of expired locks.
"""

import time

import pytest

from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit


@pytest.fixture(name="cache_storage")
def cache_storage_fixture(tmp_path):
    """Create a temporary SQLite cache storage.

    Args:
        tmp_path: Temporary directory for the SQLite database.
    """
    cache_storage = Storage(str(tmp_path / "func_cache.sqlite"))
    yield cache_storage
    cache_storage.close()


def test_storage_get_set_count_and_ttl_expiration(cache_storage):
    """Verify storage get, set, count, and TTL expiration.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    cache_storage.set("func", "key", b"value")

    assert cache_storage.get("func", "key") == b"value"
    assert cache_storage.count("func") == 1

    cache_storage.set("func", "key", b"expired", ttl=0)

    assert cache_storage.get("func", "key") is None
    assert cache_storage.count("func") == 0


def test_storage_metadata_round_trip(cache_storage):
    """Verify storage metadata round trip.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    cache_storage.set_meta("func", ["alpha", "beta"], compress=True)

    assert cache_storage.get_meta("func") == (["alpha", "beta"], True)


def test_storage_lock_lifecycle(cache_storage):
    """Verify storage lock lifecycle.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    assert cache_storage.try_acquire_lock("func", "key", "owner-1", 60)
    assert not cache_storage.try_acquire_lock("func", "key", "owner-2", 60)

    cache_storage.release_lock("func", "key", "owner-1")

    assert cache_storage.try_acquire_lock("func", "key", "owner-2", 60)


def test_storage_can_replace_expired_lock(cache_storage):
    """Verify storage can replace expired lock.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    assert cache_storage.try_acquire_lock("func", "key", "stale-owner", 0.001)

    time.sleep(0.002)

    assert cache_storage.try_acquire_lock("func", "key", "fresh-owner", 0.001)


def test_storage_list_funcs_returns_distinct_sorted(cache_storage):
    """Verify list_funcs returns distinct func_ids sorted.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    cache_storage.set("zeta", "k1", b"v")
    cache_storage.set("zeta", "k2", b"v")
    cache_storage.set("alpha", "k1", b"v")

    assert cache_storage.list_funcs() == ["alpha", "zeta"]


def test_storage_list_funcs_empty_when_no_entries(cache_storage):
    """Verify list_funcs is empty when no entries exist.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    assert cache_storage.list_funcs() == []


def test_storage_reexpire_all_to_none_makes_entries_permanent(cache_storage):
    """Verify reexpire(None, None) clears expiry on every row.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    cache_storage.set("f1", "k1", b"v", ttl=0)
    cache_storage.set("f2", "k1", b"v", ttl=0)

    rowcount = cache_storage.reexpire(None, None)

    assert rowcount == 2
    assert cache_storage.get("f1", "k1") == b"v"
    assert cache_storage.get("f2", "k1") == b"v"


def test_storage_reexpire_one_func_only_touches_that_func(cache_storage):
    """Verify reexpire(func_id, ...) only updates that func's rows.

    Args:
        cache_storage: Temporary cache storage fixture.
    """
    cache_storage.set("f1", "k1", b"v", ttl=0)
    cache_storage.set("f2", "k1", b"v", ttl=0)

    far_future = time.time() + 3600
    rowcount = cache_storage.reexpire("f1", far_future)

    assert rowcount == 1
    assert cache_storage.get("f1", "k1") == b"v"
    assert cache_storage.get("f2", "k1") is None
