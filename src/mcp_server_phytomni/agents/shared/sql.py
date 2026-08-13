# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared SQL escaping + relay helpers for BI queries across agents.

``sql_literal`` quote-escapes a value for embedding in a BI SQL string
(GaussDB has no parameterized-query surface here). ``bi_query`` is the
BI seam shared by the deep_genome and brief_gene boundaries: it runs the
SQL directly against GaussDB via ``gauss_query`` or, in customer relay
mode, forwards to ``/v1/relay/bi/query`` via ``relay_bi_query`` (the
operator runs the query server-side; no BI credential is forwarded).
"""

import asyncio
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.http import JsonPostRetry
from ...common.relay_client import RelayRequestOptions, current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...runtime.outbound import OutboundPoolName
from .gauss import gauss_query

__all__ = ["bi_query", "relay_bi_query", "sql_literal"]


async def relay_bi_query(
    sql: str,
    *,
    message: str,
    request_timeout: float | None = None,
) -> Any:
    """Run a BI SQL query through the customer relay route.

    Posts the standard ``{"sql", "returnType": "json"}`` body to
    ``/v1/relay/bi/query`` with the relay key. The relay terminates the
    query server-side against the operator's GaussDB, so a relay-mode
    child Bot needs no BI credential of its own. Shared by the brief_gene
    and deep_genome BI boundaries to keep one relay route + body shape.

    Args:
        sql: The BI SQL statement to execute.
        message: Key-free error prefix for the relay ``McpError``.
        request_timeout: Optional per-request timeout in seconds. When
            supplied, it bounds the complete relay request, including response
            parsing. ``None`` keeps the relay client's configured timeout.

    Returns:
        The parsed BI JSON payload.
    """
    relay = current_relay_client()
    request = relay.post_json(
        "bi/query",
        {"sql": sql, "returnType": "json"},
        pool=OutboundPoolName.BI,
        options=RelayRequestOptions(
            message=message,
            request_timeout=request_timeout,
        ),
    )
    if request_timeout is None:
        return await request
    try:
        return await asyncio.wait_for(request, timeout=request_timeout)
    except TimeoutError as exc:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="BI relay query timed out",
            )
        ) from exc


async def bi_query(sql: str, *, retry: JsonPostRetry) -> Any:
    """Run a BI SQL query, routing through the relay in relay mode.

    The single BI seam shared by the brief_gene and deep_genome
    boundaries: in relay mode it forwards to ``/v1/relay/bi/query`` (the
    operator runs the query server-side); otherwise it runs the SQL
    directly against GaussDB via ``gauss_query``.

    Args:
        sql: The BI SQL statement to execute.
        retry: Retry policy; its ``timeout`` bounds the direct query or relay
            request, and its ``message`` is reused as the relay error prefix.
            (The direct GaussDB path has its own error handling.)

    Returns:
        The BI JSON payload (``{"message": "ok", "data": [...]}``).
    """
    if relay_mode_enabled():
        return await relay_bi_query(
            sql,
            message=retry.message,
            request_timeout=retry.timeout,
        )
    return await gauss_query(sql, request_timeout=retry.timeout)


def sql_literal(value: str) -> str:
    """Return a single-quoted SQL literal with quote-doubling escape.

    BI's SQL dialect treats `''` as an escaped single quote inside a
    string literal, so doubling every embedded quote is the minimal
    transformation that keeps a user-supplied gene id or species code
    from breaking out of its quoted context.

    Args:
        value: Raw input destined for a SQL string literal.

    Returns:
        ``value`` wrapped in single quotes with embedded `'` escaped.
    """
    return "'" + value.replace("'", "''") + "'"
