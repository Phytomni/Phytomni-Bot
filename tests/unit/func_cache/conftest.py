# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fixtures for the func_cache unit-test package."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_server_phytomni.func_cache.storage import Storage


@pytest.fixture(name="cache_db")
def cache_db_fixture(tmp_path: Path) -> Iterator[str]:
    """Yield a temporary cache database path and close its singleton.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Yields:
        Path string for the test's ephemeral SQLite cache.
    """
    db_path = str(tmp_path / "cache.sqlite")
    yield db_path
    Storage.get_instance(db_path).close()


@pytest.fixture(name="cache_db_alpha_one_expired")
def cache_db_alpha_one_expired_fixture(cache_db: str) -> str:
    """Pre-seed ``cache_db`` with alpha k1 expired (``ttl=0``) and k2 live.

    The purge-expired tests in both ``test_cli`` and ``test_maintenance``
    share the same seed: one expired row and one open-ended row under
    the same func.

    Args:
        cache_db: Temporary cache database path.

    Returns:
        The same ``cache_db`` path, seeded with the two-row mix.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v", ttl=0)
    storage.set("alpha", "k2", b"v")
    return cache_db


@pytest.fixture(name="cache_db_two_funcs")
def cache_db_two_funcs_fixture(cache_db: str) -> str:
    """Pre-seed ``cache_db`` with alpha={k1,k2} and beta={k1}.

    Repeated across the stats / purge / reexpire tests in both
    ``test_cli`` and ``test_maintenance``; centralising the seed keeps
    every "two funcs, three live entries" assertion driven by one
    source of truth.

    Args:
        cache_db: Temporary cache database path.

    Returns:
        The same ``cache_db`` path, populated with three entries.
    """
    storage = Storage.get_instance(cache_db)
    storage.set("alpha", "k1", b"v")
    storage.set("alpha", "k2", b"v")
    storage.set("beta", "k1", b"v")
    return cache_db
