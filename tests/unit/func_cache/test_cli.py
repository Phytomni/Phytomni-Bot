# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the phytomni-cache admin CLI.

Covers stats output, full and per-func purge, reexpire with --ttl and
with --permanent, and the expired-row sweep. Each test passes --db-path
explicitly so the singleton points at a tmp_path SQLite.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.func_cache.cli import main
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit


def test_stats_prints_per_func_counts(
    cache_db_two_funcs: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify stats lists every func with its live entry count."""
    exit_code = main(["--db-path", cache_db_two_funcs, "stats"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "alpha" in out
    assert "beta" in out
    assert "2" in out
    assert "1" in out


def test_stats_empty_database_prints_marker(
    cache_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify stats over an empty database prints a sentinel and exits 0."""
    exit_code = main(["--db-path", cache_db, "stats"])

    assert exit_code == 0
    assert "no cache entries" in capsys.readouterr().out


def test_purge_one_func_drops_only_that_func(
    cache_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify purge --func-id removes only the targeted func."""
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("beta", "k1", b"v")

    exit_code = main(["--db-path", cache_db, "purge", "--func-id", "alpha"])

    assert exit_code == 0
    assert "purged 1" in capsys.readouterr().out
    assert storage.count("alpha") == 0
    assert storage.count("beta") == 1


def test_purge_without_func_id_drops_everything(
    cache_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify purge with no --func-id drops every func's entries."""
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("beta", "k1", b"v")
    storage.set("beta", "k2", b"v")

    exit_code = main(["--db-path", cache_db, "purge"])

    assert exit_code == 0
    assert "purged 3" in capsys.readouterr().out
    assert storage.list_funcs() == []


def test_reexpire_ttl_rescues_expired_entries(cache_db: str) -> None:
    """Verify reexpire --ttl rewrites expires_at on every row."""
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)

    exit_code = main(["--db-path", cache_db, "reexpire", "--ttl", "3600"])

    assert exit_code == 0
    assert storage.get("alpha", "k1") == b"v"


def test_reexpire_permanent_drops_expiry_on_one_func(cache_db: str) -> None:
    """Verify reexpire --permanent --func-id clears TTL for one func."""
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)
    storage.set("beta", "k1", b"v", ttl=0)

    exit_code = main(
        [
            "--db-path",
            cache_db,
            "reexpire",
            "--permanent",
            "--func-id",
            "alpha",
        ]
    )

    assert exit_code == 0
    assert storage.get("alpha", "k1") == b"v"
    assert storage.get("beta", "k1") is None


def test_purge_expired_removes_expired_only(
    cache_db_alpha_one_expired: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify purge-expired drops ttl=0 rows but leaves open-ended rows."""
    exit_code = main(
        ["--db-path", cache_db_alpha_one_expired, "purge-expired"]
    )

    assert exit_code == 0
    assert "expired entries purged" in capsys.readouterr().out
    assert Storage.get_instance(cache_db_alpha_one_expired).count("alpha") == 1


def test_reexpire_requires_ttl_or_permanent(cache_db: str) -> None:
    """Verify reexpire fails when no --ttl/--permanent is given."""
    with pytest.raises(SystemExit):
        main(["--db-path", cache_db, "reexpire"])
