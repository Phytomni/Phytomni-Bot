# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral setup helpers for relay route and forwarding tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay.routes import create_relay_router
from tests.support.http_fakes import open_asgi_client

__all__ = [
    "patch_mock_transport",
    "make_relay_client_fixture",
    "make_relay_key_fixture",
    "make_relay_reset_fixture",
    "build_relay_app",
    "relay_key_factory",
    "reset_relay_inflight",
]


def build_relay_app() -> FastAPI:
    """Mount the production relay router on a bare test application."""
    app = FastAPI()
    app.include_router(create_relay_router())
    return app


def patch_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Patch a relay module with a no-network outbound runtime fake."""

    class _RequestClient:
        """Use a fresh MockTransport client for each isolated attempt."""

        async def request(
            self, method: str, url: str, **kwargs: Any
        ) -> httpx.Response:
            """Run one buffered mocked request."""
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.request(method, url, **kwargs)

        @asynccontextmanager
        async def stream(
            self, method: str, url: str, **kwargs: Any
        ) -> AsyncIterator[httpx.Response]:
            """Run one streamed mocked request."""
            async with (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                yield response

    class _HttpRuntime:
        """Return one isolated fake for either relay HTTP profile."""

        def __init__(self) -> None:
            self.client = _RequestClient()

        def for_pool(
            self, _pool: object, *, profile: object = None
        ) -> _RequestClient:
            """Return the selected fixed-profile fake."""
            del profile
            return self.client

        @asynccontextmanager
        async def stream(
            self,
            _pool: object,
            method: str,
            url: str,
            *,
            profile: object = None,
            **kwargs: Any,
        ) -> AsyncIterator[httpx.Response]:
            """Return one streamed response through the selected profile."""
            del profile
            async with self.client.stream(method, url, **kwargs) as response:
                yield response

    runtime = type("RelayRuntime", (), {"http": _HttpRuntime()})()
    monkeypatch.setattr(
        module, "current_outbound_runtime", lambda: runtime, raising=False
    )


def relay_key_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_id: str,
) -> Callable[[str], str]:
    """Return a scoped relay-key factory backed by a temporary store."""
    db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(db))
    return lambda service: store.create(
        user_id=user_id, scopes=[f"relay:{service}"]
    ).api_key


def make_relay_key_fixture(
    *, user_id: str
) -> Callable[..., Callable[[str], str]]:
    """Build a pytest fixture for one relay test tenant."""

    @pytest.fixture(name="relay_key")
    def _relay_key(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Callable[[str], str]:
        return relay_key_factory(tmp_path, monkeypatch, user_id=user_id)

    return _relay_key


def make_relay_client_fixture(
    app_factory: Callable[[], FastAPI],
) -> Callable[..., object]:
    """Build a pytest fixture for one relay ASGI application."""

    @pytest.fixture(name="client")
    async def _client(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> AsyncIterator[httpx.AsyncClient]:
        monkeypatch.setenv(
            "PHYTOMNI_RELAY_AUDIT_DB_PATH",
            str(tmp_path / "relay-audit.sqlite"),
        )
        async with open_asgi_client(
            monkeypatch,
            app_factory(),
            base_url="http://relay.test",
        ) as client:
            yield client

    return _client


def make_relay_reset_fixture(module: ModuleType) -> Callable[..., object]:
    """Build an autouse fixture that clears one relay module's counter."""

    @pytest.fixture(autouse=True)
    def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
        reset_relay_inflight(monkeypatch, module)

    return _reset_inflight


def reset_relay_inflight(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> None:
    """Clear the relay concurrency counter between route tests."""
    monkeypatch.setattr(module, "_INFLIGHT", {})
