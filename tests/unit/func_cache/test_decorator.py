# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the func_cache decorator.

Covers sync and async cache hits, TTL expiry, exception handling, default cache
paths, excluded parameters, corrupted values, and concurrent miss locking.
"""

import asyncio
from typing import Any

import pytest

import mcp_server_phytomni.func_cache as func_cache_pkg
from mcp_server_phytomni.func_cache.decorator import (
    LONG_TTL_SECONDS,
    default_cache_db_path,
    func_cache,
)
from mcp_server_phytomni.func_cache.key_builder import KeyBuilder
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit


def test_func_cache_reuses_result_and_exposes_info(tmp_path):
    """Verify func_cache reuses result and exposes info.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after cache info assertions pass.
    """
    calls: dict[str, Any] = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "decorator.sqlite"),
        key_params=["value"],
        ttl=60,
    )
    def double(value, noise=None):
        """Return doubled value while counting calls.

        Args:
            value: Numeric value to double.
            noise: Ignored value outside the cache key.

        Returns:
            Payload containing doubled value and call count.
        """
        calls["count"] += 1
        calls["noise"] = noise
        return {"value": value * 2, "call": calls["count"]}

    first = double(2, noise="leaf")
    second = double(2, noise="root")

    assert first == second == {"value": 4, "call": 1}
    assert calls["count"] == 1
    assert double.cache_info() == {"hits": 1, "misses": 1, "count": 1}

    double.cache_clear()

    assert double.cache_info()["count"] == 0
    assert double(2)["call"] == 2


def test_func_cache_respects_zero_ttl(tmp_path):
    """Verify func_cache respects zero ttl.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after recomputation assertions pass.
    """
    calls = {"count": 0}

    @func_cache(db_path=str(tmp_path / "ttl.sqlite"), ttl=0)
    def next_value():
        """Return a monotonically increasing value.

        Returns:
            Next call count.
        """
        calls["count"] += 1
        return calls["count"]

    assert next_value() == 1
    assert next_value() == 2


def test_func_cache_does_not_cache_exceptions(tmp_path):
    """Verify func_cache does not cache exceptions.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after retry assertions pass.
    """
    calls = {"count": 0}

    @func_cache(db_path=str(tmp_path / "exceptions.sqlite"))
    def flaky_value():
        """Raise once and then return ok.

        Returns:
            Static success marker after the first failure.
        """
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("boom")
        return "ok"

    with pytest.raises(ValueError, match="boom"):
        flaky_value()

    assert flaky_value() == "ok"
    assert calls["count"] == 2


def test_func_cache_default_db_path_uses_environment(
    monkeypatch,
    tmp_path,
):
    """Verify func_cache default db path uses environment.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to set env vars.
        tmp_path: Temporary directory for the configured cache path.

    Returns:
        None after cache path assertions pass.
    """
    db_path = tmp_path / "nested" / "env-cache.sqlite"
    monkeypatch.setenv("PHYTOMNI_CACHE_DB", str(db_path))

    @func_cache()
    def identity(value):
        """Return the received value.

        Args:
            value: Value to return.

        Returns:
            Received value.
        """
        return value

    assert default_cache_db_path() == str(db_path)
    assert identity("leaf") == "leaf"
    assert db_path.exists()


def test_func_cache_default_db_path_uses_project_cache_dir(
    monkeypatch,
    tmp_path,
):
    """Verify func_cache default db path uses project cache dir.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to clear env and chdir.
        tmp_path: Temporary project root.

    Returns:
        None after project cache path assertions pass.
    """
    monkeypatch.delenv("PHYTOMNI_CACHE_DB", raising=False)
    monkeypatch.chdir(tmp_path)

    @func_cache()
    def identity(value):
        """Return the received value.

        Args:
            value: Value to return.

        Returns:
            Received value.
        """
        return value

    assert identity("root") == "root"
    assert (tmp_path / ".cache" / "phytomni" / "func_cache.sqlite").exists()


def test_func_cache_exclude_params_can_skip_clients(tmp_path):
    """Verify func_cache exclude params can skip clients.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after excluded-client cache assertions pass.
    """
    calls = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "exclude.sqlite"),
        exclude_params=["client"],
    )
    def fetch(value, client=None):
        """Return a cached payload while optionally calling a client.

        Args:
            value: Cache-key value.
            client: Optional infrastructure callback excluded from the key.

        Returns:
            Payload containing value and call count.
        """
        calls["count"] += 1
        if client is not None:
            client(value)
        return {"value": value, "call": calls["count"]}

    first = fetch("leaf", client=lambda value: value)
    second = fetch("leaf", client=lambda value: value)

    assert first == second == {"value": "leaf", "call": 1}
    assert calls["count"] == 1


def test_func_cache_deletes_corrupted_values_and_recomputes(tmp_path):
    """Verify func_cache deletes corrupted values and recomputes.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after recomputation assertions pass.
    """
    calls = {"count": 0}
    db_path = tmp_path / "corrupted.sqlite"

    @func_cache(db_path=str(db_path), key_params=["value"])
    def cached_value(value):
        """Return a cached payload for corruption tests.

        Args:
            value: Cache-key value.

        Returns:
            Payload containing value and call count.
        """
        calls["count"] += 1
        return {"value": value, "call": calls["count"]}

    assert cached_value("leaf") == {"value": "leaf", "call": 1}

    original = cached_value.__wrapped__
    key_builder = KeyBuilder(original, key_params=["value"])
    cache_key = key_builder.build_key(("leaf",), {})
    storage = Storage.get_instance(str(db_path))
    storage.set(key_builder.func_id, cache_key, b"not-a-pickle")

    assert cached_value("leaf") == {"value": "leaf", "call": 2}
    assert calls["count"] == 2


async def test_func_cache_supports_async_round_trip(tmp_path):
    """Verify func_cache supports async round trip.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after async cache assertions pass.
    """
    calls: dict[str, Any] = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "async.sqlite"),
        key_params=["value"],
    )
    async def double(value, noise=None):
        """Return doubled value asynchronously while counting calls.

        Args:
            value: Numeric value to double.
            noise: Ignored value outside the cache key.

        Returns:
            Payload containing doubled value and call count.
        """
        calls["count"] += 1
        calls["noise"] = noise
        await asyncio.sleep(0)
        return {"value": value * 2, "call": calls["count"]}

    first = await double(2, noise="leaf")
    second = await double(2, noise="root")

    assert first == second == {"value": 4, "call": 1}
    assert calls["count"] == 1
    assert double.cache_info() == {"hits": 1, "misses": 1, "count": 1}


async def test_func_cache_async_does_not_cache_exceptions(tmp_path):
    """Verify func_cache async does not cache exceptions.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after async retry assertions pass.
    """
    calls = {"count": 0}

    @func_cache(db_path=str(tmp_path / "async-exceptions.sqlite"))
    async def flaky_value():
        """Raise once and then return ok asynchronously.

        Returns:
            Static success marker after the first failure.
        """
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("boom")
        return "ok"

    with pytest.raises(ValueError, match="boom"):
        await flaky_value()

    assert await flaky_value() == "ok"
    assert calls["count"] == 2


async def test_func_cache_async_concurrent_miss_runs_once(tmp_path):
    """Verify func_cache async concurrent miss runs once.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after concurrent cache assertions pass.
    """
    calls = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "async-concurrent.sqlite"),
        key_params=["value"],
        lock_timeout=1,
    )
    async def expensive(value):
        """Return a delayed payload while counting calls.

        Args:
            value: Cache-key value.

        Returns:
            Payload containing value and call count.
        """
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return {"value": value, "call": calls["count"]}

    results = await asyncio.gather(
        expensive("leaf"),
        expensive("leaf"),
        expensive("leaf"),
    )

    assert results == [
        {"value": "leaf", "call": 1},
        {"value": "leaf", "call": 1},
        {"value": "leaf", "call": 1},
    ]
    assert calls["count"] == 1
    assert expensive.cache_info() == {"hits": 2, "misses": 1, "count": 1}


def test_long_ttl_seconds_is_ninety_days_and_exported():
    """Verify the shared long TTL constant value and package export.

    Returns:
        None after the constant equals 90 days and is re-exported.
    """
    assert LONG_TTL_SECONDS == 90 * 24 * 3600
    assert func_cache_pkg.LONG_TTL_SECONDS is LONG_TTL_SECONDS
    assert "LONG_TTL_SECONDS" in func_cache_pkg.__all__
