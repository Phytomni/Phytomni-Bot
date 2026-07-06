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
    def __init__(self, rows: list[dict[str, Any]], boom: bool) -> None:
        """Store rows and boom flag; start with closed=False."""
        self._rows = rows
        self._boom = boom
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yield a connection stub that returns rows or raises on boom."""
        rows, boom = self._rows, self._boom

        async def fetch(_sql: str) -> list[dict[str, Any]]:
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
) -> list[_FakePool]:
    made: list[_FakePool] = []

    async def fake_create_pool(*_a: Any, **_k: Any) -> _FakePool:
        pool = _FakePool(rows, boom)
        made.append(pool)
        return pool

    monkeypatch.setattr(gauss_mod.asyncpg, "create_pool", fake_create_pool)
    _GAUSS_POOL_STATE.clear()
    return made


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
    made = _patch_pool(monkeypatch, [{"x": 1}])

    asyncio.run(gauss_query("SELECT 1"))
    asyncio.run(gauss_query("SELECT 1"))

    assert len(made) == 2
    assert made[0] is not made[1]


async def test_aclose_gauss_pool_closes_and_evicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aclose closes the loop's pool and drops it from the registry."""
    made = _patch_pool(monkeypatch, [{"x": 1}])
    await gauss_query("SELECT 1")
    assert len(_GAUSS_POOL_STATE) == 1

    await aclose_gauss_pool()

    assert made[0].closed is True
    assert len(_GAUSS_POOL_STATE) == 0
