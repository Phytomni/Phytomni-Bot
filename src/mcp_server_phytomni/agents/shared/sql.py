# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared SQL escaping + relay helpers for BI queries across agents.

The BI backend has no parameterized-query surface, so ``sql_literal``
lets callers (deep_genome lookups and the brief_gene gene retriever
today) embed an identifier or string value inside a SQL statement
without smuggling extra single quotes — and therefore arbitrary clauses
— through user input. ``relay_bi_query`` is the single seam those same
callers use in customer relay mode to route a BI SQL query through the
relay instead of the operator ``BI_URL``.
"""

from typing import Any, Mapping

from httpx import Timeout

from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ...common.httpx_client import get_async_client
from ...common.relay_client import current_relay_client
from ...config.relay_mode import relay_mode_enabled

__all__ = ["bi_query", "relay_bi_query", "sql_literal"]


async def relay_bi_query(sql: str, *, message: str) -> Any:
    """Run a BI SQL query through the customer relay route.

    Posts the standard ``{"sql", "returnType": "json"}`` body to
    ``/v1/relay/bi/query`` with the relay key. The relay injects the
    operator ``BI_TOKEN`` (a ``bi``-inject route), so a relay-mode child
    Bot needs no BI credential of its own. Shared by the brief_gene and
    deep_genome BI boundaries to keep one relay route + body shape.

    Args:
        sql: The BI SQL statement to execute.
        message: Key-free error prefix for the relay ``McpError``.

    Returns:
        The parsed BI JSON payload.
    """
    return await current_relay_client().post_json(
        "bi/query",
        json_body={"sql": sql, "returnType": "json"},
        message=message,
    )


async def bi_query(
    sql: str,
    *,
    bi_url: str,
    headers: Mapping[str, str],
    retry: JsonPostRetry,
) -> Any:
    """Run a BI SQL query, routing through the relay in relay mode.

    The single BI POST seam shared by the brief_gene and deep_genome
    boundaries: in relay mode it forwards to ``/v1/relay/bi/query`` (the
    relay injects ``BI_TOKEN``); otherwise it posts the standard
    ``{"sql", "returnType": "json"}`` body to the operator ``bi_url``
    with the caller's headers and retry policy.

    Args:
        sql: The BI SQL statement to execute.
        bi_url: Operator BI endpoint (ignored in relay mode).
        headers: Operator request headers incl. the BI token (ignored in
            relay mode).
        retry: Retry policy; its ``message`` is reused as the relay
            error prefix.

    Returns:
        The parsed BI JSON payload.
    """
    if relay_mode_enabled():
        return await relay_bi_query(sql, message=retry.message)
    client_timeout = Timeout(retry.timeout, connect=retry.timeout)
    async with get_async_client(timeout=client_timeout) as client:
        return await post_json_with_retries(
            client,
            JsonPostRequest(
                url=bi_url,
                headers=dict(headers),
                json_body={"sql": sql, "returnType": "json"},
            ),
            retry,
        )


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
