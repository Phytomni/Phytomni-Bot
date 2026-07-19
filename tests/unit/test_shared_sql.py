# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the shared BI routing and timeout contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.shared import sql as shared_sql
from mcp_server_phytomni.common.http import JsonPostRetry

pytestmark = [pytest.mark.unit, pytest.mark.agent]


def _retry(request_timeout: float) -> JsonPostRetry:
    """Return a minimal retry policy for the shared BI seam."""
    return JsonPostRetry(
        timeout=request_timeout,
        max_retries=0,
        retriable_codes=(),
        message="BI query failed",
    )


async def test_bi_query_passes_retry_timeout_to_direct_gauss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct path receives the caller's timeout unchanged."""
    captured: dict[str, Any] = {}

    async def fake_gauss_query(
        sql: str,
        *,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        captured.update(sql=sql, request_timeout=request_timeout)
        return {"message": "ok", "data": []}

    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.setattr(shared_sql, "gauss_query", fake_gauss_query)

    result = await shared_sql.bi_query("SELECT 1", retry=_retry(12.5))

    assert result == {"message": "ok", "data": []}
    assert captured == {"sql": "SELECT 1", "request_timeout": 12.5}


async def test_bi_query_passes_retry_timeout_to_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relay path receives the same timeout policy as direct BI."""
    captured: dict[str, Any] = {}

    async def fake_relay_bi_query(
        sql: str,
        *,
        message: str,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        captured.update(
            sql=sql, message=message, request_timeout=request_timeout
        )
        return {"message": "ok", "data": []}

    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setattr(shared_sql, "relay_bi_query", fake_relay_bi_query)

    result = await shared_sql.bi_query("SELECT 2", retry=_retry(8.0))

    assert result == {"message": "ok", "data": []}
    assert captured == {
        "sql": "SELECT 2",
        "message": "BI query failed",
        "request_timeout": 8.0,
    }


async def test_relay_bi_query_timeout_is_key_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled relay request is bounded without exposing credentials."""
    captured: dict[str, Any] = {}

    async def slow_post_json(
        _path: str,
        *,
        json_body: Any,
        message: str,
        request_timeout: float | None = None,
    ) -> Any:
        captured.update(
            path=_path,
            body=json_body,
            message=message,
            request_timeout=request_timeout,
        )
        _ = (json_body, message, request_timeout)
        await asyncio.sleep(0.05)
        return {"message": "ok", "data": []}

    monkeypatch.setattr(
        shared_sql,
        "current_relay_client",
        lambda: SimpleNamespace(post_json=slow_post_json),
    )

    with pytest.raises(McpError, match="BI relay query timed out") as excinfo:
        await shared_sql.relay_bi_query(
            "SELECT 3",
            message="BI query failed",
            request_timeout=0.001,
        )

    assert "Authorization" not in excinfo.value.error.message
    assert "secret" not in excinfo.value.error.message
    assert captured == {
        "path": "bi/query",
        "body": {"sql": "SELECT 3", "returnType": "json"},
        "message": "BI query failed",
        "request_timeout": 0.001,
    }


async def test_relay_bi_query_without_timeout_keeps_legacy_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing relay clients remain valid when no timeout is requested."""

    async def legacy_post_json(
        _path: str,
        *,
        json_body: Any,
        message: str,
    ) -> Any:
        _ = (json_body, message)
        return {"message": "ok", "data": []}

    monkeypatch.setattr(
        shared_sql,
        "current_relay_client",
        lambda: SimpleNamespace(post_json=legacy_post_json),
    )

    result = await shared_sql.relay_bi_query(
        "SELECT 4",
        message="BI query failed",
    )

    assert result == {"message": "ok", "data": []}
