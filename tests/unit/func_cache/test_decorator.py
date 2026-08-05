# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the func_cache decorator.

Covers sync and async cache hits, TTL expiry, exception handling, default cache
paths, excluded parameters, corrupted values, and concurrent miss locking.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

import mcp_server_phytomni.func_cache as func_cache_pkg
from mcp_server_phytomni.func_cache.decorator import (
    LONG_TTL_SECONDS,
    default_cache_db_path,
    func_cache,
)
from mcp_server_phytomni.func_cache.exceptions import StorageError
from mcp_server_phytomni.func_cache.key_builder import KeyBuilder
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit

_CACHE_LOGGER_NAME = "mcp_server_phytomni.func_cache.core"


@pytest.fixture(name="cache_caplog")
def _cache_caplog_fixture(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[pytest.LogCaptureFixture]:
    """Capture cache warnings even after package logging is configured."""
    with capture_non_propagating_logger(
        _CACHE_LOGGER_NAME,
        caplog.handler,
    ):
        yield caplog


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


def test_func_cache_rejects_invalid_cache_if_predicates():
    """Reject non-callable and asynchronous admission predicates.

    Returns:
        None after public option validation assertions pass.
    """

    async def async_predicate(_result):
        """Return an asynchronous admission decision."""
        return True

    expected = "cache_if must be a synchronous callable"
    with pytest.raises(TypeError, match=expected):
        func_cache(cache_if=True)
    with pytest.raises(TypeError, match=expected):
        func_cache(cache_if=async_predicate)


def test_func_cache_fails_closed_for_wrapped_async_predicate(
    tmp_path,
    cache_caplog,
):
    """Reject awaitables returned by a synchronous predicate wrapper.

    Args:
        tmp_path: Temporary directory for the cache database.
        cache_caplog: Cache logger capture isolated from package logging.

    Returns:
        None after runtime predicate validation assertions pass.
    """
    calls = {"count": 0}

    async def async_predicate(_result):
        """Return an asynchronous admission decision."""
        return True

    def wrapped_predicate(result):
        """Return the async predicate's coroutine without awaiting it."""
        return async_predicate(result)

    @func_cache(
        db_path=str(tmp_path / "wrapped-async-cache-if.sqlite"),
        cache_if=wrapped_predicate,
    )
    def fetch():
        """Return a fresh value for each rejected cache attempt."""
        calls["count"] += 1
        return {"call": calls["count"]}

    assert fetch() == {"call": 1}
    assert fetch() == {"call": 2}
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 0, "misses": 2, "count": 0}
    assert "Cache admission policy returned an awaitable" in cache_caplog.text


def test_func_cache_cache_if_skips_rejected_sync_result(tmp_path):
    """Return a rejected result but cache a later accepted value.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after result admission assertions pass.
    """
    calls = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "cache-if-sync.sqlite"),
        key_params=["value"],
        cache_if=lambda result: result["cacheable"],
    )
    def fetch(value):
        """Return the next result for one cache key.

        Args:
            value: Cache-key value.

        Returns:
            Result carrying its cache admission marker and call number.
        """
        calls["count"] += 1
        return {
            "value": value,
            "cacheable": calls["count"] > 1,
            "call": calls["count"],
        }

    assert fetch("leaf")["call"] == 1
    assert fetch("leaf")["call"] == 2
    assert fetch("leaf")["call"] == 2
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 1, "misses": 2, "count": 1}


def test_func_cache_cache_if_evicts_rejected_sync_hit(tmp_path):
    """Evict a historical value after its admission policy changes.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after lazy cache eviction assertions pass.
    """
    policy = {"reject_error": False}
    calls = {"count": 0}

    def cache_if(result):
        """Accept historical errors only while the permissive policy is on."""
        return not policy["reject_error"] or "error" not in result

    @func_cache(
        db_path=str(tmp_path / "cache-if-sync-hit.sqlite"),
        cache_if=cache_if,
    )
    def fetch():
        """Return one historical error followed by a valid result."""
        calls["count"] += 1
        if calls["count"] == 1:
            return {"error": "legacy"}
        return {"choices": ["recovered"]}

    assert fetch() == {"error": "legacy"}
    policy["reject_error"] = True
    assert fetch() == {"choices": ["recovered"]}
    assert fetch() == {"choices": ["recovered"]}
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 1, "misses": 2, "count": 1}


def test_func_cache_cache_if_exception_fails_closed(tmp_path, cache_caplog):
    """Reject cache values when their admission predicate raises.

    Args:
        tmp_path: Temporary directory for the cache database.
        cache_caplog: Cache logger capture isolated from package logging.

    Returns:
        None after fail-closed and redaction assertions pass.
    """
    policy = {"raise": False}
    calls = {"count": 0}

    def cache_if(_result):
        """Raise only after the historical value has been stored."""
        if policy["raise"]:
            raise RuntimeError("provider-body-must-not-log")
        return True

    @func_cache(
        db_path=str(tmp_path / "cache-if-exception.sqlite"),
        cache_if=cache_if,
    )
    def fetch():
        """Return a fresh call number for each rejected cache attempt."""
        calls["count"] += 1
        return {"call": calls["count"]}

    assert fetch() == {"call": 1}
    policy["raise"] = True
    assert fetch() == {"call": 2}
    assert fetch() == {"call": 3}

    assert calls["count"] == 3
    assert fetch.cache_info() == {"hits": 0, "misses": 3, "count": 0}
    assert "Cache admission policy failed: RuntimeError" in cache_caplog.text
    assert "provider-body-must-not-log" not in cache_caplog.text


def test_func_cache_rejected_delete_error_still_recomputes(
    tmp_path,
    monkeypatch,
    cache_caplog,
):
    """Recompute when exact cleanup of a rejected cache entry fails.

    Args:
        tmp_path: Temporary directory for the cache database.
        monkeypatch: Pytest monkeypatch fixture for the storage boundary.
        cache_caplog: Cache logger capture isolated from package logging.

    Returns:
        None after cleanup fallback and redaction assertions pass.
    """
    policy = {"reject_error": False}
    calls = {"count": 0}

    def cache_if(result):
        """Reject the historical error after the policy changes."""
        return not policy["reject_error"] or "error" not in result

    @func_cache(
        db_path=str(tmp_path / "cache-if-delete-error.sqlite"),
        cache_if=cache_if,
    )
    def fetch():
        """Return one historical error followed by a valid value."""
        calls["count"] += 1
        if calls["count"] == 1:
            return {"error": "legacy"}
        return {"choices": ["recovered"]}

    assert fetch() == {"error": "legacy"}
    policy["reject_error"] = True

    def fail_delete(
        _storage: Storage,
        _func_id: str,
        _cache_key: str,
    ) -> None:
        """Raise a bounded storage failure at the delete boundary."""
        raise StorageError("cached-body-must-not-log")

    monkeypatch.setattr(Storage, "delete_entry", fail_delete)

    assert fetch() == {"choices": ["recovered"]}
    assert fetch() == {"choices": ["recovered"]}
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 1, "misses": 2, "count": 1}
    assert (
        "Cache rejected entry cleanup failed: StorageError"
        in cache_caplog.text
    )
    assert "cached-body-must-not-log" not in cache_caplog.text


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


async def test_func_cache_cache_if_converges_after_rejected_async_hit(
    tmp_path,
):
    """Converge concurrent callers after rejecting an async cache hit.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after concurrent admission assertions pass.
    """
    policy = {"reject_error": False}
    calls = {"count": 0}

    def cache_if(result):
        """Accept historical errors only while the permissive policy is on."""
        return not policy["reject_error"] or "error" not in result

    @func_cache(
        db_path=str(tmp_path / "cache-if-async-hit.sqlite"),
        cache_if=cache_if,
        lock_timeout=1,
    )
    async def fetch():
        """Return one historical error followed by a valid async result."""
        calls["count"] += 1
        await asyncio.sleep(0.01)
        if calls["count"] == 1:
            return {"error": "legacy"}
        return {"choices": ["recovered"], "call": calls["count"]}

    assert await fetch() == {"error": "legacy"}
    policy["reject_error"] = True
    results = await asyncio.gather(fetch(), fetch(), fetch())

    assert results == [
        {"choices": ["recovered"], "call": 2},
        {"choices": ["recovered"], "call": 2},
        {"choices": ["recovered"], "call": 2},
    ]
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 2, "misses": 2, "count": 1}


async def test_func_cache_cache_if_skips_rejected_async_result(tmp_path):
    """Return a rejected async result but cache a later accepted value.

    Args:
        tmp_path: Temporary directory for the cache database.

    Returns:
        None after async result admission assertions pass.
    """
    calls = {"count": 0}

    @func_cache(
        db_path=str(tmp_path / "cache-if-async.sqlite"),
        cache_if=lambda result: result["cacheable"],
    )
    async def fetch():
        """Return the next asynchronous admission candidate."""
        calls["count"] += 1
        return {
            "cacheable": calls["count"] > 1,
            "call": calls["count"],
        }

    assert await fetch() == {"cacheable": False, "call": 1}
    assert await fetch() == {"cacheable": True, "call": 2}
    assert await fetch() == {"cacheable": True, "call": 2}
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 1, "misses": 2, "count": 1}


async def test_func_cache_cache_if_exception_fails_closed_async(
    tmp_path,
    cache_caplog,
):
    """Reject async cache values when their admission predicate raises.

    Args:
        tmp_path: Temporary directory for the cache database.
        cache_caplog: Cache logger capture isolated from package logging.

    Returns:
        None after async fail-closed and redaction assertions pass.
    """
    policy = {"raise": False}
    calls = {"count": 0}

    def cache_if(_result):
        """Raise only after the historical value has been stored."""
        if policy["raise"]:
            raise RuntimeError("async-provider-body-must-not-log")
        return True

    @func_cache(
        db_path=str(tmp_path / "cache-if-async-exception.sqlite"),
        cache_if=cache_if,
    )
    async def fetch():
        """Return a fresh call number for each rejected cache attempt."""
        calls["count"] += 1
        return {"call": calls["count"]}

    assert await fetch() == {"call": 1}
    policy["raise"] = True
    assert await fetch() == {"call": 2}
    assert await fetch() == {"call": 3}

    assert calls["count"] == 3
    assert fetch.cache_info() == {"hits": 0, "misses": 3, "count": 0}
    assert "Cache admission policy failed: RuntimeError" in cache_caplog.text
    assert "async-provider-body-must-not-log" not in cache_caplog.text


async def test_func_cache_rejected_delete_error_still_recomputes_async(
    tmp_path,
    monkeypatch,
    cache_caplog,
):
    """Recompute async values when rejected-entry cleanup fails.

    Args:
        tmp_path: Temporary directory for the cache database.
        monkeypatch: Pytest monkeypatch fixture for the storage boundary.
        cache_caplog: Cache logger capture isolated from package logging.

    Returns:
        None after async cleanup fallback assertions pass.
    """
    policy = {"reject_error": False}
    calls = {"count": 0}

    def cache_if(result):
        """Reject the historical error after the policy changes."""
        return not policy["reject_error"] or "error" not in result

    @func_cache(
        db_path=str(tmp_path / "cache-if-async-delete-error.sqlite"),
        cache_if=cache_if,
    )
    async def fetch():
        """Return one historical error followed by a valid value."""
        calls["count"] += 1
        if calls["count"] == 1:
            return {"error": "legacy"}
        return {"choices": ["recovered"]}

    assert await fetch() == {"error": "legacy"}
    policy["reject_error"] = True

    def fail_delete(
        _storage: Storage,
        _func_id: str,
        _cache_key: str,
    ) -> None:
        """Raise a bounded storage failure at the delete boundary."""
        raise StorageError("async-cached-body-must-not-log")

    monkeypatch.setattr(Storage, "delete_entry", fail_delete)

    assert await fetch() == {"choices": ["recovered"]}
    assert await fetch() == {"choices": ["recovered"]}
    assert calls["count"] == 2
    assert fetch.cache_info() == {"hits": 1, "misses": 2, "count": 1}
    assert (
        "Cache rejected entry cleanup failed: StorageError"
        in cache_caplog.text
    )
    assert "async-cached-body-must-not-log" not in cache_caplog.text


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
