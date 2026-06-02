# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OpenAI-family relay routes (llm / coder / embed).

Each route is scope-gated, reads its body under the byte budget, strips
the caller credential and injects the operator's Bearer key, and forwards
to a config-resolved upstream URL with the client query dropped.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay import routes as routes_module
from mcp_server_phytomni.api.relay.routes import create_relay_router

pytestmark = pytest.mark.server

_REAL_REQUEST = httpx.AsyncClient.request


def _build_app() -> FastAPI:
    """Mount the relay router on a bare app for route testing."""
    app = FastAPI()
    app.include_router(create_relay_router())
    return app


def _patch_upstream(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Point the forwarding core at a MockTransport-backed client."""

    @contextlib.asynccontextmanager
    async def _factory(
        **_kwargs: object,
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(forward_module, "get_async_client", _factory)


def _patch_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide fake operator LLM/coder/embed secrets to the routes."""
    fake = SimpleNamespace(
        BASE_URL="https://llm.test/v1",
        API_KEY=SecretStr("sk-llm-op"),
        CODER_URL="https://coder.test/api/v2",
        CODER_API_KEY=SecretStr("sk-coder-op"),
        EMBED_URL="https://embed.test/v1/",
        EMBED_API_KEY=SecretStr("sk-embed-op"),
    )
    monkeypatch.setattr(routes_module, "get_sensitive_config", lambda: fake)


@pytest.fixture(name="relay_key")
def _relay_key_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str], str]:
    """Return a factory minting a key scoped for one relay service."""
    db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(db))
    return lambda svc: store.create(
        user_id="c", scopes=[f"relay:{svc}"]
    ).api_key


@pytest.fixture(name="client")
async def _client_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an httpx client bound to the relay app over ASGI."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://relay.test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


async def test_llm_route_injects_bearer_and_drops_query(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The llm route strips the caller key, injects the operator Bearer,
    and forwards to the config URL without the client query string."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    _patch_upstream(monkeypatch, handler)
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/llm/chat/completions?inject=evil",
        headers={"Authorization": f"Bearer {relay_key('llm')}"},
        content=b'{"q":1}',
    )

    assert response.status_code == 200
    assert seen[0].headers["authorization"] == "Bearer sk-llm-op"
    # Upstream URL is config-resolved; the client query is dropped ([12]).
    assert str(seen[0].url) == "https://llm.test/v1/chat/completions"


async def test_embed_route_uses_embeddings_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embed route targets EMBED_URL + /embeddings with its key."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    _patch_upstream(monkeypatch, handler)
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/embed/embeddings",
        headers={"Authorization": f"Bearer {relay_key('embed')}"},
        content=b'{"input":"x"}',
    )

    assert response.status_code == 200
    assert seen[0].headers["authorization"] == "Bearer sk-embed-op"
    assert str(seen[0].url) == "https://embed.test/v1/embeddings"


async def test_llm_route_rejects_wrong_scope(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An embed-scoped key cannot reach the llm route."""
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/llm/chat/completions",
        headers={"Authorization": f"Bearer {relay_key('embed')}"},
        content=b"{}",
    )

    assert response.status_code == 403
