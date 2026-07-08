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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.shared import gauss as gauss_mod
from mcp_server_phytomni.agents.shared.gauss import (
    _GAUSS_POOL_STATE,
    aclose_gauss_pool,
    gauss_query,
)

pytestmark = [pytest.mark.unit, pytest.mark.agent]


class _FakePool:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        boom: bool,
        timeout_boom: bool = False,
    ) -> None:
        """Store rows and flags; start with closed=False."""
        self._rows = rows
        self._boom = boom
        self._timeout_boom = timeout_boom
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yield a connection stub that returns rows or raises on a flag."""
        rows, boom, timeout_boom = self._rows, self._boom, self._timeout_boom

        async def fetch(_sql: str) -> list[dict[str, Any]]:
            if timeout_boom:
                raise TimeoutError("command timeout")
            if boom:
                raise asyncpg.PostgresError("bad sql")
            return rows

        yield SimpleNamespace(fetch=fetch)

    async def close(self) -> None:
        """Mark pool as closed."""
        self.closed = True


def _patch_pool(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    boom: bool = False,
    timeout_boom: bool = False,
) -> tuple[list[_FakePool], dict[str, Any]]:
    made: list[_FakePool] = []
    captured: dict[str, Any] = {}

    async def fake_create_pool(*_a: Any, **kwargs: Any) -> _FakePool:
        captured.update(kwargs)
        pool = _FakePool(rows, boom, timeout_boom)
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


def test_pool_is_per_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each event loop gets its own pool (asyncpg pools are loop-bound)."""
    made, _ = _patch_pool(monkeypatch, [{"x": 1}])

    asyncio.run(gauss_query("SELECT 1"))
    asyncio.run(gauss_query("SELECT 1"))

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

        async def default_reset() -> None:
            # Models conn.reset() -> get_reset_query() -> UNLISTEN * on
            # a server that lacks UNLISTEN (GaussDB).
            raise asyncpg.exceptions.FeatureNotSupportedError(
                "UNLISTEN is not yet supported."
            )

        conn = SimpleNamespace(fetch=fetch)
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
        return _ResetModelingPool(rows, kwargs.get("reset"))

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

    assert not any(
        "UNLISTEN" in stmt.upper() for stmt in executed
    ), f"reset issued UNLISTEN: {executed}"
