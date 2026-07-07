# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Direct GaussDB query seam (replaces the gaussapp HTTP hairpin).

``gauss_query`` returns the gaussapp-identical envelope so callers need no
change. The pool is per-event-loop (``WeakKeyDictionary``) because asyncpg
pools are loop-bound. Driver errors surface as ``McpError``; the relay's
``{"message": "sql error"}`` envelope lives only at the HTTP edge.
"""

from __future__ import annotations

import asyncio
from typing import Any
from weakref import WeakKeyDictionary

import asyncpg
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...config.defaults import ServerConfig
from ...config.settings import get_sensitive_config

__all__ = ["aclose_gauss_pool", "gauss_query"]

_GAUSS_POOL_STATE: WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncpg.Pool
] = WeakKeyDictionary()


async def _gauss_pool() -> asyncpg.Pool:
    """Return the current loop's asyncpg pool, creating it on first use.

    The pool is keyed by the running loop so the project's MCP serve
    loop, API lifespan loop, and per-test loops each get their own; a
    WeakKeyDictionary evicts the entry when a loop is garbage-collected.
    """
    loop = asyncio.get_running_loop()
    pool = _GAUSS_POOL_STATE.get(loop)
    if pool is None:
        dsn = get_sensitive_config().GAUSS_DSN.get_secret_value()
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=5,
            max_size=20,
            command_timeout=ServerConfig().GAUSS_COMMAND_TIMEOUT,
        )
        _GAUSS_POOL_STATE[loop] = pool
    return pool


async def gauss_query(sql: str) -> dict[str, Any]:
    """Run one read-only SQL statement against GaussDB.

    Args:
        sql: The SQL statement to execute.

    Returns:
        ``{"message": "ok", "data": [<row dict>, ...]}`` — byte-identical
        to the gaussapp envelope (NULL -> None, text -> str, int -> int).

    Raises:
        McpError: On any asyncpg driver / connection error (including
            command timeouts, which surface as asyncio.TimeoutError —
            a subclass of OSError), mirroring the failure shape the
            prior BI HTTP retry path raised.
    """
    try:
        pool = await _gauss_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
    except (asyncpg.PostgresError, OSError) as exc:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"GaussDB query failed: {exc}",
            )
        ) from exc
    return {"message": "ok", "data": [dict(row) for row in rows]}


async def aclose_gauss_pool() -> None:
    """Close and evict the current loop's pool (lifespan teardown).

    Safe to call when no pool exists for the loop, so a teardown can run
    unconditionally next to ``aclose_shared_client``.
    """
    loop = asyncio.get_running_loop()
    pool = _GAUSS_POOL_STATE.pop(loop, None)
    if pool is not None:
        await pool.close()
