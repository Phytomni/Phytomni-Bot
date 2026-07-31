# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the direct GaussDB query seam (agents/shared/gauss).

All offline: asyncpg.create_pool is patched with a fake pool, so no real
database socket is ever opened (block_external_http does not catch
asyncpg). Covers the ok envelope, empty set, NULL/UTF-8 fidelity, the
bad-SQL -> McpError contract, per-loop pool isolation, and aclose.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import asyncpg
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.shared import gauss as gauss_mod
from mcp_server_phytomni.agents.shared.gauss import (
    _GAUSS_POOL_STATE,
    _gauss_reset,
    aclose_gauss_pool,
    gauss_query,
)

pytestmark = [pytest.mark.unit, pytest.mark.agent]


class _FakePool:
    _fetch_delay = 0.0

    def __init__(
        self,
        rows: list[dict[str, Any]],
        boom: bool,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Store rows and flags; start with closed=False."""
        timeout_boom = kwargs.pop("timeout_boom", False)
        error_message = kwargs.pop("error_message", "bad sql")
        transaction_error = kwargs.pop("transaction_error", None)
        if kwargs:
            raise TypeError("unknown fake-pool options")
        if len(args) > 3:
            raise TypeError("too many fake-pool options")
        if args:
            timeout_boom = args[0]
        if len(args) > 1:
            error_message = args[1]
        if len(args) > 2:
            transaction_error = args[2]
        self._rows = rows
        self._boom = boom
        self._timeout_boom = timeout_boom
        self._error_message = error_message
        self._transaction_error = transaction_error
        self.closed = False
        self.last_connection: Any | None = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yield a connection stub that returns rows or raises on a flag."""
        rows, boom, timeout_boom = self._rows, self._boom, self._timeout_boom
        fetch_delay = self._fetch_delay
        events: list[str] = []
        transaction_calls: list[dict[str, Any]] = []

        fetch_calls: list[dict[str, Any]] = []

        async def fetch(_sql: str, **kwargs: Any) -> list[dict[str, Any]]:
            fetch_calls.append(kwargs)
            events.append("fetch")
            if fetch_delay:
                await asyncio.sleep(fetch_delay)
            if timeout_boom:
                raise TimeoutError("command timeout")
            if boom:
                raise asyncpg.PostgresError(self._error_message)
            return rows

        @asynccontextmanager
        async def transaction(**kwargs: Any) -> AsyncIterator[None]:
            transaction_calls.append(kwargs)
            if self._transaction_error is not None:
                raise self._transaction_error
            events.append("transaction_enter")
            try:
                yield None
            finally:
                events.append("transaction_exit")

        self.last_connection = SimpleNamespace(
            fetch=fetch,
            transaction=transaction,
            events=events,
            fetch_calls=fetch_calls,
            transaction_calls=transaction_calls,
        )
        yield self.last_connection

    async def close(self) -> None:
        """Mark pool as closed."""
        self.closed = True

    def set_transaction_error(self, error: BaseException) -> None:
        """Configure a transaction error for a focused failure test."""
        self._transaction_error = error


def _patch_pool(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    boom: bool = False,
    timeout_boom: bool = False,
    error_message: str = "bad sql",
) -> tuple[list[_FakePool], dict[str, Any]]:
    made: list[_FakePool] = []
    captured: dict[str, Any] = {}

    async def fake_create_pool(*_a: Any, **kwargs: Any) -> _FakePool:
        captured.update(kwargs)
        pool = _FakePool(rows, boom, timeout_boom, error_message)
        transaction_error = captured.get("transaction_error")
        if isinstance(transaction_error, BaseException):
            pool.set_transaction_error(transaction_error)
        made.append(pool)
        return pool

    monkeypatch.setattr(gauss_mod.asyncpg, "create_pool", fake_create_pool)
    _GAUSS_POOL_STATE.clear()
    return made, captured


async def test_gauss_query_returns_ok_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful query returns the gaussapp-shaped ok envelope."""
    _patch_pool(monkeypatch, [{"gene_id": "Os01g0100100", "n": 3}])
    result = await gauss_query("SELECT 1")
    assert result == {
        "message": "ok",
        "data": [{"gene_id": "Os01g0100100", "n": 3}],
    }


async def test_gauss_query_empty_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty result set yields message=ok with an empty data list."""
    _patch_pool(monkeypatch, [])
    assert await gauss_query("SELECT 1 WHERE false") == {
        "message": "ok",
        "data": [],
    }


async def test_gauss_query_preserves_null_and_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NULL stays None and UTF-8 text round-trips as str."""
    _patch_pool(monkeypatch, [{"symbol": None, "name": "玉米"}])
    rows = (await gauss_query("SELECT 1"))["data"]
    assert rows[0]["symbol"] is None
    assert rows[0]["name"] == "玉米"


async def test_gauss_query_bad_sql_raises_mcperror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver error surfaces as McpError, matching today's HTTP path."""
    _patch_pool(monkeypatch, [], boom=True)
    with pytest.raises(McpError):
        await gauss_query("SELECT bogus")


async def test_unsafe_sql_is_rejected_before_pool_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsafe SQL fails before asyncpg creates or acquires a pool."""
    pool = _FakePool([], boom=False)
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(gauss_mod.asyncpg, "create_pool", create_pool)
    _GAUSS_POOL_STATE.clear()

    with pytest.raises(McpError, match="read-only query"):
        await gauss_query("DELETE FROM secret_table")

    create_pool.assert_not_awaited()


async def test_driver_error_does_not_leak_sql_or_dsn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Driver failures expose fixed text and metadata-only diagnostics."""
    _patch_pool(
        monkeypatch,
        [],
        boom=True,
        error_message="postgresql://u:p@host/db secret_column",
    )
    monkeypatch.setattr(
        gauss_mod,
        "current_request_id",
        lambda: "req-gauss",
        raising=False,
    )
    # The package logger intentionally disables propagation in production;
    # re-enable it here so pytest's root-attached caplog sees the record.
    package_logger = logging.getLogger("mcp_server_phytomni")
    monkeypatch.setattr(package_logger, "propagate", True)

    with (
        caplog.at_level(logging.ERROR, logger=gauss_mod.__name__),
        pytest.raises(McpError) as exc_info,
    ):
        await gauss_query("SELECT secret_column")

    combined = f"{exc_info.value.error.message}\n{caplog.text}"
    assert exc_info.value.error.message == "GaussDB query failed"
    assert "postgresql://" not in combined
    assert "secret_column" not in combined
    assert "req-gauss" in caplog.text
    assert "PostgresError" in caplog.text


async def test_gauss_query_fetches_inside_read_only_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validated queries enter a read-only transaction before fetch."""
    made, _captured = _patch_pool(monkeypatch, [{"x": 1}])

    result = await gauss_query("SELECT 1 AS x")

    connection = made[0].last_connection
    assert connection is not None
    assert result == {"message": "ok", "data": [{"x": 1}]}
    assert connection.transaction_calls == [{"readonly": True}]
    assert connection.events == [
        "transaction_enter",
        "fetch",
        "transaction_exit",
    ]


async def test_read_only_transaction_failure_never_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transaction setup failure is sanitized and prevents fetch."""
    made, captured = _patch_pool(monkeypatch, [])
    captured["transaction_error"] = RuntimeError("unsupported")

    with pytest.raises(McpError, match="GaussDB query failed"):
        await gauss_query("SELECT 1")

    connection = made[0].last_connection
    assert connection is not None
    assert "fetch" not in connection.events


async def test_gauss_query_preserves_cancellation_during_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation during transaction setup propagates unchanged."""
    made, captured = _patch_pool(monkeypatch, [])
    captured["transaction_error"] = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await gauss_query("SELECT 1")

    connection = made[0].last_connection
    assert connection is not None
    assert "fetch" not in connection.events


def test_pool_is_per_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each event loop gets its own pool (asyncpg pools are loop-bound)."""
    made, _ = _patch_pool(monkeypatch, [{"x": 1}])

    def run_in_fresh_loop() -> None:
        """Run one pool probe on an explicitly closed event loop."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(gauss_query("SELECT 1"))
        finally:
            loop.close()

    run_in_fresh_loop()
    run_in_fresh_loop()

    assert len(made) == 2
    assert made[0] is not made[1]


async def test_aclose_gauss_pool_closes_and_evicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aclose closes the loop's pool and drops it from the registry."""
    made, _ = _patch_pool(monkeypatch, [{"x": 1}])
    await gauss_query("SELECT 1")
    assert len(_GAUSS_POOL_STATE) == 1

    await aclose_gauss_pool()

    assert made[0].closed is True
    assert len(_GAUSS_POOL_STATE) == 0


async def test_gauss_pool_sets_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_pool receives command_timeout from ServerConfig (default 30)."""
    _made, captured = _patch_pool(monkeypatch, [{"x": 1}])
    await gauss_query("SELECT 1")
    assert captured["command_timeout"] == 30.0


async def test_gauss_query_command_timeout_raises_mcperror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command timeout (asyncio.TimeoutError) surfaces as McpError.

    TimeoutError is a subclass of OSError, so the existing OSError
    entry in the except tuple already covers this case. This test
    pins that contract so a future except-tuple tightening cannot
    silently break command-timeout handling.
    """
    _patch_pool(monkeypatch, [], timeout_boom=True)
    with pytest.raises(McpError):
        await gauss_query("SELECT slow")


async def test_gauss_query_passes_explicit_timeout_to_asyncpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller timeout overrides asyncpg's pool command default."""
    made, _captured = _patch_pool(monkeypatch, [{"x": 1}])

    result = await gauss_query("SELECT 1", request_timeout=7.5)

    connection = made[0].last_connection
    assert connection is not None
    assert result == {"message": "ok", "data": [{"x": 1}]}
    assert connection.fetch_calls == [{"timeout": 7.5}]


async def test_gauss_query_timeout_bounds_the_full_pool_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller timeout bounds a slow fetch, not only driver arguments."""
    _patch_pool(monkeypatch, [])
    monkeypatch.setattr(_FakePool, "_fetch_delay", 0.05)

    with pytest.raises(McpError, match="GaussDB query failed"):
        await gauss_query("SELECT slow", request_timeout=0.001)


class _ResetModelingPool:
    """Fake pool that models asyncpg's connection-release reset branch.

    asyncpg ``pool.py`` releases a connection two ways (release-time
    ``_ConnectionProxy._release``): when ``create_pool`` got a custom
    ``reset`` coroutine it runs ``conn._reset()`` then that coroutine;
    otherwise it runs ``conn.reset()``, whose ``get_reset_query()``
    emits ``UNLISTEN *`` on a notifications-capable server. GaussDB
    advertises notifications yet has no ``UNLISTEN``, so the default
    path raises ``FeatureNotSupportedError`` when the connection is
    returned to the pool. This fake reproduces exactly that fork so the
    bug is offline-reproducible without a real GaussDB.
    """

    def __init__(self, rows: list[dict[str, Any]], reset: Any) -> None:
        """Store rows and the ``reset`` coroutine create_pool received."""
        self._rows = rows
        self._reset = reset
        self.executed: list[str] = []
        self.reset_error: BaseException | None = None
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yield a conn stub, then run the modeled release-time reset.

        The reset fires in ``__aexit__`` (mirroring ``async with
        pool.acquire()``), which is where the real UNLISTEN failure
        surfaces after the rows are already fetched.
        """
        rows = self._rows

        async def fetch(_sql: str) -> list[dict[str, Any]]:
            return rows

        @asynccontextmanager
        async def transaction(**_kwargs: Any) -> AsyncIterator[None]:
            yield None

        async def execute(sql: str) -> None:
            self.executed.append(sql)
            if self.reset_error is not None:
                raise self.reset_error

        async def default_reset() -> None:
            # Models conn.reset() -> get_reset_query() -> UNLISTEN * on
            # a server that lacks UNLISTEN (GaussDB).
            raise asyncpg.exceptions.FeatureNotSupportedError(
                "UNLISTEN is not yet supported."
            )

        conn = SimpleNamespace(
            fetch=fetch,
            transaction=transaction,
            execute=execute,
        )
        try:
            yield conn
        finally:
            if self._reset is not None:
                await self._reset(conn)
            else:
                await default_reset()

    async def close(self) -> None:
        """Mark pool as closed."""
        self.closed = True


def _patch_reset_modeling_pool(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Patch create_pool with a release-branch-modeling fake pool.

    Returns the captured create_pool kwargs so a test can assert the
    ``reset`` coroutine was supplied and exercise it directly.
    """
    captured: dict[str, Any] = {}

    async def fake_create_pool(*_a: Any, **kwargs: Any) -> _ResetModelingPool:
        captured.update(kwargs)
        pool = _ResetModelingPool(rows, kwargs.get("reset"))
        reset_error = captured.get("reset_error")
        if isinstance(reset_error, BaseException):
            pool.reset_error = reset_error
        captured["pool"] = pool
        return pool

    monkeypatch.setattr(gauss_mod.asyncpg, "create_pool", fake_create_pool)
    _GAUSS_POOL_STATE.clear()
    return captured


async def test_gauss_query_survives_pool_release_without_unlisten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pooled acquire->fetch->release round-trip must not raise.

    On the deployed GaussDB, asyncpg's default connection reset issues
    ``UNLISTEN *`` when the connection returns to the pool, which the
    server rejects with FeatureNotSupportedError -- discarding the rows
    already fetched. The fix supplies a ``reset`` coroutine that omits
    UNLISTEN; this pins that a full round-trip returns the ok envelope
    instead of raising.
    """
    _patch_reset_modeling_pool(monkeypatch, [{"n": 1}])

    result = await gauss_query("SELECT 1")

    assert result == {"message": "ok", "data": [{"n": 1}]}


async def test_gauss_reset_executes_only_reset_all() -> None:
    """The pool reset clears session state without issuing UNLISTEN."""
    connection = AsyncMock()

    await _gauss_reset(connection)

    connection.execute.assert_awaited_once_with("RESET ALL")
    assert "UNLISTEN" not in str(connection.execute.await_args)


async def test_reset_failure_never_reclassifies_query_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reset error propagates through the sanitized query failure path."""
    captured = _patch_reset_modeling_pool(monkeypatch, [{"n": 1}])
    captured["reset_error"] = asyncpg.PostgresError("reset body")

    with pytest.raises(McpError, match="GaussDB query failed"):
        await gauss_query("SELECT 1")

    pool = cast(_ResetModelingPool, captured["pool"])
    assert pool.executed == ["RESET ALL"]


class _SingleConnectionPool:
    """Minimal one-connection pool used to model borrower GUC isolation."""

    def __init__(self) -> None:
        """Start with no session settings."""
        self.settings: dict[str, str] = {}

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yield one connection and reset it when the borrower returns it."""

        async def execute(sql: str) -> None:
            """Apply the only session commands used by this fake."""
            normalized = sql.strip().upper()
            if normalized == "RESET ALL":
                self.settings.clear()
                return
            key, _, value = sql[4:].partition("=")
            self.settings[key.strip()] = value.strip().strip("'")

        connection = SimpleNamespace(execute=execute)
        yield connection
        await _gauss_reset(cast(asyncpg.Connection, connection))

    async def borrow_and_set(self, key: str, value: str) -> None:
        """Set a session value for one borrower."""
        async with self.acquire() as connection:
            await connection.execute(f"SET {key} = '{value}'")

    async def borrow_and_show(self, key: str) -> str | None:
        """Read a session value for the next borrower."""
        async with self.acquire():
            return self.settings.get(key)


async def test_second_borrower_does_not_see_first_guc() -> None:
    """RESET ALL prevents one connection borrower inheriting a GUC."""
    pool = _SingleConnectionPool()

    await pool.borrow_and_set("application_name", "borrower-one")

    assert await pool.borrow_and_show("application_name") != "borrower-one"


async def test_gauss_pool_supplies_non_unlisten_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_pool receives a reset coroutine that emits no UNLISTEN.

    Pins the fix mechanism: a ``reset`` callback is passed (so asyncpg
    takes the custom-reset branch that skips ``conn.reset()``'s
    UNLISTEN), and invoking it drives no ``UNLISTEN`` statement through
    the connection.
    """
    captured = _patch_reset_modeling_pool(monkeypatch, [{"n": 1}])
    await gauss_query("SELECT 1")

    reset = captured.get("reset")
    assert reset is not None, "no reset= passed; default UNLISTEN path used"

    executed: list[str] = []

    async def record_execute(sql: str) -> None:
        executed.append(sql)

    await reset(SimpleNamespace(execute=record_execute))

    assert executed == ["RESET ALL"]
    assert not any("UNLISTEN" in stmt.upper() for stmt in executed)
