# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Error and reconnect edges for SQLite-backed func_cache storage."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

from mcp_server_phytomni.func_cache.exceptions import StorageError
from mcp_server_phytomni.func_cache.storage import Storage

pytestmark = pytest.mark.unit

_STORAGE_LOGGER = "mcp_server_phytomni.func_cache.storage"


@pytest.fixture(name="cache_storage")
def cache_storage_fixture(tmp_path) -> Iterator[Storage]:
    """Create a temporary SQLite cache storage.

    Args:
        tmp_path: Temporary directory for the SQLite database.

    Yields:
        Isolated cache storage for one test.
    """
    cache_storage = Storage(str(tmp_path / "func_cache.sqlite"))
    yield cache_storage
    cache_storage.close()


class _FailingConn:
    """Wrap a live SQLite handle so execute/close can raise."""

    def __init__(
        self,
        real: sqlite3.Connection,
        *,
        fail_execute: bool = True,
        fail_close: bool = False,
    ) -> None:
        self._real = real
        self._fail_execute = fail_execute
        self._fail_close = fail_close

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        """Optionally raise instead of running SQL."""
        if self._fail_execute:
            raise sqlite3.Error("disk i/o error")
        return self._real.execute(sql, *args, **kwargs)

    def close(self) -> None:
        """Optionally raise instead of closing the handle."""
        if self._fail_close:
            raise sqlite3.Error("close failed")
        self._real.close()


def _install_failing_conn(
    storage: Storage,
    *,
    fail_execute: bool = True,
    fail_close: bool = False,
) -> _FailingConn:
    """Swap the thread-local handle for a failing wrapper."""
    real = getattr(storage, "_get_conn")()
    getattr(Storage, "_connections").pop(id(real), None)
    fake = _FailingConn(real, fail_execute=fail_execute, fail_close=fail_close)
    getattr(storage, "_local").conn = fake
    getattr(Storage, "_connections")[id(fake)] = cast(
        tuple[Storage, sqlite3.Connection], (storage, fake)
    )
    return fake


def _fail_sql(storage: Storage) -> None:
    """Replace the live connection execute with a SQLite failure."""
    _install_failing_conn(storage)


def test_storage_raises_when_cache_directory_cannot_be_created(
    tmp_path,
) -> None:
    """A non-directory parent path becomes a StorageError."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not-a-directory", encoding="utf-8")
    with pytest.raises(StorageError, match="Failed to create cache directory"):
        Storage(str(blocker / "cache.sqlite"))


def test_storage_connect_error_closes_partial_connection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PRAGMA failure after connect still closes the partial handle."""
    real_connect = sqlite3.connect

    def _connect_then_fail(*args: Any, **kwargs: Any) -> _FailingConn:
        conn = real_connect(*args, **kwargs)
        return _FailingConn(conn, fail_execute=True, fail_close=True)

    monkeypatch.setattr(sqlite3, "connect", _connect_then_fail)
    with pytest.raises(StorageError, match="Failed to connect to SQLite"):
        Storage(str(tmp_path / "cache.sqlite"))


def test_storage_reconnects_after_pid_change(
    cache_storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forked PID drops the inherited thread-local connection."""
    first = getattr(cache_storage, "_get_conn")()
    cache_storage.set("func", "key", b"value")
    new_pid = getattr(cache_storage, "_pid") + 1
    monkeypatch.setattr(os, "getpid", lambda: new_pid)
    second = getattr(cache_storage, "_get_conn")()
    assert getattr(cache_storage, "_pid") == new_pid
    assert second is not first
    assert cache_storage.get("func", "key") == b"value"


def test_storage_replaces_unregistered_thread_local_connection(
    cache_storage: Storage,
) -> None:
    """A stale thread-local handle is discarded when unregistered."""
    conn = getattr(cache_storage, "_get_conn")()
    getattr(Storage, "_connections").pop(id(conn), None)
    replacement = getattr(cache_storage, "_get_conn")()
    assert replacement is not conn


def test_storage_close_all_logs_connection_close_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Leftover registry handles that fail to close are logged."""

    class _BoomConn:
        def close(self) -> None:
            """Raise the close error close_all must swallow."""
            raise sqlite3.Error("stale close")

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "boom-conn"

    getattr(Storage, "_connections")[id(_BoomConn)] = cast(
        tuple[Storage, sqlite3.Connection],
        (None, _BoomConn()),
    )
    with capture_non_propagating_logger(_STORAGE_LOGGER, caplog.handler):
        Storage.close_all()
    assert "Failed to close cache connection" in caplog.text


def test_storage_init_db_error_is_storage_error(
    cache_storage: Storage,
) -> None:
    """Schema creation failures wrap sqlite3.Error."""
    _fail_sql(cache_storage)
    with pytest.raises(StorageError, match="Failed to initialize database"):
        getattr(cache_storage, "_init_db")()


@pytest.mark.parametrize(
    ("invoke", "match"),
    [
        (lambda storage: storage.get("func", "key"), "Failed to read cache"),
        (
            lambda storage: storage.set("func", "key", b"v"),
            "Failed to write cache",
        ),
        (
            lambda storage: storage.delete_entry("func", "key"),
            "Failed to delete cache entry",
        ),
        (
            lambda storage: storage.delete_func("func"),
            "Failed to delete function cache",
        ),
        (
            lambda storage: storage.count("func"),
            "Failed to count cache entries",
        ),
        (
            lambda storage: storage.purge_expired(),
            "Failed to purge expired cache",
        ),
        (
            lambda storage: storage.list_funcs(),
            "Failed to list cached funcs",
        ),
        (
            lambda storage: storage.reexpire("func", None),
            "Failed to reexpire cache",
        ),
        (
            lambda storage: storage.get_meta("func"),
            "Failed to read metadata",
        ),
        (
            lambda storage: storage.set_meta("func", ["a"], False),
            "Failed to write metadata",
        ),
        (
            lambda storage: storage.release_lock("func", "key", "owner"),
            "Failed to release lock",
        ),
        (
            lambda storage: storage.cleanup_process_locks(1),
            "Failed to cleanup process locks",
        ),
        (
            lambda storage: storage.cleanup_func_locks("func"),
            "Failed to cleanup function locks",
        ),
    ],
)
def test_storage_sql_errors_wrap_sqlite_failures(
    cache_storage: Storage,
    invoke: Callable[[Storage], object],
    match: str,
) -> None:
    """Each storage mutator maps sqlite3.Error onto StorageError."""
    _fail_sql(cache_storage)
    with pytest.raises(StorageError, match=match):
        invoke(cache_storage)


def test_storage_try_acquire_lock_rolls_back_and_drops_dead_conn(
    cache_storage: Storage,
) -> None:
    """A lock-transaction failure rolls back and discards a dead handle."""
    _install_failing_conn(cache_storage, fail_execute=True, fail_close=True)
    with pytest.raises(StorageError, match="Failed to acquire lock"):
        cache_storage.try_acquire_lock("func", "key", "owner", 60)
    assert getattr(getattr(cache_storage, "_local"), "conn", "missing") is None


def test_storage_close_logs_cleanup_and_close_errors(
    cache_storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shutdown keeps going when lock cleanup or close fails."""
    _install_failing_conn(cache_storage, fail_execute=False, fail_close=True)

    def _fail_cleanup(_pid: int) -> None:
        raise StorageError("cleanup failed")

    monkeypatch.setattr(cache_storage, "cleanup_process_locks", _fail_cleanup)
    with capture_non_propagating_logger(_STORAGE_LOGGER, caplog.handler):
        cache_storage.close()
    assert "Failed to clean up process locks" in caplog.text
    assert "Failed to close cache connection" in caplog.text


def test_storage_get_instance_reuses_resolved_path(tmp_path) -> None:
    """The process singleton is keyed by the resolved database path."""
    path = tmp_path / "shared.sqlite"
    first = Storage.get_instance(str(path))
    second = Storage.get_instance(path)
    assert first is second


def test_storage_get_meta_returns_none_for_unknown_func(
    cache_storage: Storage,
) -> None:
    """Missing metadata is a silent miss, not an error."""
    assert cache_storage.get_meta("missing") is None


def test_storage_delete_entry_and_purge_expired_succeed(
    cache_storage: Storage,
) -> None:
    """Happy-path delete and expired-row sweep stay available."""
    cache_storage.set("func", "live", b"keep")
    cache_storage.set("func", "old", b"drop", ttl=0)
    cache_storage.delete_entry("func", "live")
    cache_storage.purge_expired()
    assert cache_storage.get("func", "live") is None
    assert cache_storage.get("func", "old") is None
