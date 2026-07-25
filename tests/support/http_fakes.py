# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""ASGI HTTP-client fixtures shared by offline server tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from mcp_server_phytomni import server

__all__ = [
    "assert_duplicate_attachment_response",
    "assert_degraded_tracking_response",
    "install_tool_handler",
    "install_rejection_handler",
    "open_asgi_client",
]

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


def install_tool_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    handler: Any,
) -> None:
    """Install one handler in the shared MCP tool registry."""
    monkeypatch.setitem(server.TOOL_HANDLERS, tool_name, handler)


def install_rejection_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> dict[str, bool]:
    """Install a tracked handler that must not run after validation."""
    marker = {"called": False}

    async def fake(_args: Any) -> dict[str, Any]:
        marker["called"] = True
        return {"answer": "must not run", "doc_list": []}

    install_tool_handler(monkeypatch, tool_name, fake)
    return marker


def assert_duplicate_attachment_response(response: httpx.Response) -> None:
    """Assert the common duplicate-attachment HTTP error projection."""
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_duplicate"


def assert_degraded_tracking_response(
    response: httpx.Response,
    task_id: str,
) -> dict[str, Any]:
    """Assert the shared 202 response for accepted work without a row."""
    assert response.status_code == 202
    body = response.json()
    assert body["id"] is None
    assert body["task_ids"] == [task_id]
    assert body["degraded_tracking"] is True
    assert "run_id" not in body
    return body


def minimal_tool_handler(
    answer: str,
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    """Return a handler with the minimal MCP tabular result shape."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"answer": answer, "doc_list": []}

    return fake


def tracked_tool_handler(
    result: dict[str, Any],
) -> tuple[dict[str, bool], Callable[[Any], Awaitable[dict[str, Any]]]]:
    """Return a handler plus a mutable call marker for rejection tests."""
    marker = {"called": False}

    async def fake(_args: Any) -> dict[str, Any]:
        marker["called"] = True
        return result

    return marker, fake


@asynccontextmanager
async def open_asgi_client(
    monkeypatch: pytest.MonkeyPatch,
    app: ASGIApp,
    *,
    base_url: str,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an in-process client after restoring real request dispatch."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=base_url,
    ) as client:
        yield client
