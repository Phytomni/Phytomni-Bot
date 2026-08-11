# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Serving-boundary ownership tests for the outbound runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI

from mcp_server_phytomni.api import app_support
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.runtime.outbound import OutboundRuntimeStateError

pytestmark = pytest.mark.server


@pytest.mark.asyncio
async def test_http_lifespan_starts_and_clears_outbound_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI lifespan publishes runtime only for the serving interval."""
    events: list[str] = []
    active = False

    async def init() -> None:
        nonlocal active
        active = True
        events.append("init")

    async def close() -> None:
        nonlocal active
        active = False
        events.append("close")

    monkeypatch.setattr(
        app_support, "validate_citation_database", lambda: None
    )
    monkeypatch.setattr(app_support, "init_outbound_runtime", init)
    monkeypatch.setattr(app_support, "aclose_outbound_runtime", close)
    monkeypatch.setattr(
        app_support, "refresh_research_relay_capability", _async_noop
    )
    monkeypatch.setattr(
        app_support, "ensure_research_input_runtime", lambda: None
    )
    monkeypatch.setattr(app_support, "recover_registered_startup", _async_noop)
    monkeypatch.setattr(app_support, "aclose_gauss_pool", _async_noop)

    http_lifespan = getattr(app_support, "_http_lifespan")
    async with http_lifespan(FastAPI()):
        assert active is True
    assert active is False
    assert events == ["init", "close"]


@pytest.mark.asyncio
async def test_http_startup_failure_clears_outbound_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after runtime publication still runs owned cleanup."""
    events: list[str] = []

    async def init() -> None:
        events.append("init")

    async def fail_refresh(*_args: Any) -> None:
        raise RuntimeError("refresh failed")

    async def close() -> None:
        events.append("close")

    monkeypatch.setattr(
        app_support,
        "validate_citation_database",
        lambda: None,
    )
    monkeypatch.setattr(app_support, "init_outbound_runtime", init)
    monkeypatch.setattr(
        app_support, "refresh_research_relay_capability", fail_refresh
    )
    monkeypatch.setattr(app_support, "aclose_outbound_runtime", close)
    monkeypatch.setattr(app_support, "aclose_gauss_pool", _async_noop)

    http_lifespan = getattr(app_support, "_http_lifespan")
    with pytest.raises(RuntimeError, match="refresh failed"):
        async with http_lifespan(FastAPI()):
            pytest.fail("failed startup unexpectedly yielded")

    assert events == ["init", "close"]


@pytest.mark.asyncio
async def test_mcp_serve_starts_and_clears_outbound_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP stdio owns the same runtime lifecycle around its serving loop."""
    events: list[str] = []

    async def init() -> None:
        events.append("init")

    async def close() -> None:
        events.append("close")

    class Server:
        """Minimal MCP server fake."""

        def list_tools(self) -> Any:
            """Return a pass-through list-tools decorator."""
            return lambda function: function

        def call_tool(self) -> Any:
            """Return a pass-through call-tool decorator."""
            return lambda function: function

        def create_initialization_options(self) -> object:
            """Return a minimal initialization-options object."""
            return object()

        async def run(self, *_args: Any, **_kwargs: Any) -> None:
            """Record that the MCP serving loop ran."""
            events.append("run")

    @asynccontextmanager
    async def stdio() -> AsyncIterator[tuple[object, object]]:
        yield object(), object()

    monkeypatch.setattr(mcp_app, "configure_logging", lambda: None)
    monkeypatch.setattr(mcp_app, "validate_citation_database", lambda: None)
    monkeypatch.setattr(mcp_app, "Server", lambda _name: Server())
    monkeypatch.setattr(mcp_app, "stdio_server", stdio)
    monkeypatch.setattr(mcp_app, "init_outbound_runtime", init)
    monkeypatch.setattr(mcp_app, "aclose_outbound_runtime", close)
    monkeypatch.setattr(mcp_app, "aclose_gauss_pool", _async_noop)

    await mcp_app.serve()

    assert events == ["init", "run", "close"]


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    """Return an awaitable no-op for serving-boundary seams."""


def test_missing_runtime_error_is_publicly_typed() -> None:
    """The lifecycle module exposes one stable state error type."""
    assert issubclass(OutboundRuntimeStateError, RuntimeError)
