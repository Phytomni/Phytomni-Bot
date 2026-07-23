# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""ASGI HTTP-client fixtures shared by offline server tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.types import ASGIApp

__all__ = ["open_asgi_client"]

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


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
