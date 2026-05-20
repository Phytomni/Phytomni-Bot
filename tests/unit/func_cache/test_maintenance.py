# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the func_cache maintenance layer.

Covers per-func stats, full and per-func purging with returned counts,
TTL rewrites that rescue expired rows or scope to one func, and the
expired-entry sweep.
"""

import time

import pytest

from mcp_server_phytomni.func_cache.maintenance import (
    cache_stats,
    purge_all,
    purge_expired_entries,
    purge_func,
    reexpire_all,
    reexpire_func,
)
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit


def test_cache_stats_returns_per_func_counts(cache_db):
    """Verify cache_stats reports a live count per known func_id.

    Args:
        cache_db: Temporary cache database path.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("alpha", "k2", b"v")
    storage.set("beta", "k1", b"v")

    assert cache_stats(db_path=cache_db) == {"alpha": 2, "beta": 1}


def test_purge_func_returns_count_and_removes_entries(cache_db):
    """Verify purge_func returns the pre-purge count and clears rows.

    Args:
        cache_db: Temporary cache database path.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("alpha", "k2", b"v")
    storage.set("beta", "k1", b"v")

    purged = purge_func("alpha", db_path=cache_db)

    assert purged == 2
    assert cache_stats(db_path=cache_db) == {"beta": 1}


def test_purge_all_returns_total_and_clears_database(cache_db):
    """Verify purge_all returns the total live count and clears all rows.

    Args:
        cache_db: Temporary cache database path.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("beta", "k1", b"v")
    storage.set("beta", "k2", b"v")

    purged = purge_all(db_path=cache_db)

    assert purged == 3
    assert cache_stats(db_path=cache_db) == {}


def test_reexpire_all_with_none_makes_entries_permanent(cache_db):
    """Verify reexpire_all(None) rescues already-expired entries.

    Args:
        cache_db: Temporary cache database path.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)
    storage.set("beta", "k1", b"v", ttl=0)

    rowcount = reexpire_all(None, db_path=cache_db)

    assert rowcount == 2
    assert storage.get("alpha", "k1") == b"v"
    assert storage.get("beta", "k1") == b"v"


def test_reexpire_func_only_extends_one_func(cache_db):
    """Verify reexpire_func only updates the targeted func's rows.

    Args:
        cache_db: Temporary cache database path.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)
    storage.set("beta", "k1", b"v", ttl=0)

    rowcount = reexpire_func("alpha", 3600, db_path=cache_db)

    assert rowcount == 1
    assert storage.get("alpha", "k1") == b"v"
    assert storage.get("beta", "k1") is None


def test_purge_expired_entries_removes_expired_rows(
    cache_db_alpha_one_expired,
):
    """Verify purge_expired_entries clears already-expired rows.

    Args:
        cache_db_alpha_one_expired: Cache pre-seeded with alpha k1
            expired and alpha k2 live.
    """
    time.sleep(0.001)

    purge_expired_entries(db_path=cache_db_alpha_one_expired)

    assert cache_stats(db_path=cache_db_alpha_one_expired) == {"alpha": 1}
