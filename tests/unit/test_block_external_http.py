# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline coverage for the root ``block_external_http`` send guard."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from tests.support.http_fakes import _REAL_ASYNC_REQUEST

pytestmark = pytest.mark.unit


async def test_async_send_on_default_client_is_blocked() -> None:
    """A default HTTPX client cannot open a live socket via send."""
    async with httpx.AsyncClient() as client:
        request = client.build_request("GET", "https://example.invalid/")
        with pytest.raises(RuntimeError, match="HTTP requests are disabled"):
            await asyncio.wait_for(client.send(request), timeout=1.0)


async def test_restored_request_still_blocks_live_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring request for ASGI must not unguard live HTTP send."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="HTTP requests are disabled"):
            await asyncio.wait_for(
                client.get("https://example.invalid/"),
                timeout=1.0,
            )


async def test_mock_transport_send_stays_open() -> None:
    """In-process MockTransport send remains available offline."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.send(
            client.build_request("GET", "https://example.invalid/")
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
