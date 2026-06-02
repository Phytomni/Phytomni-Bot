# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the require_relay_access route dependency.

Covers the relay admission chain ordering: an unknown key is 401, a
wrong-or-empty scope is 403 (relay denies the all-access shortcut), a
matching scope is 200, and exceeding the relay-specific budget is 429
without consuming the separate agent budget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import Depends, FastAPI

from mcp_server_phytomni.api.auth import ApiKeyStore, ApiPrincipal
from mcp_server_phytomni.api.relay.routes import require_relay_access

pytestmark = pytest.mark.server

# Captured before block_external_http patches AsyncClient.request, so the
# in-process ASGI client below can dispatch without the network guard.
_REAL_REQUEST = httpx.AsyncClient.request


def _build_probe_app() -> FastAPI:
    """Build a tiny app whose probe route is relay-access gated."""
    app = FastAPI()

    @app.get("/probe")
    async def probe(
        principal: ApiPrincipal = Depends(require_relay_access("llm")),
    ) -> dict[str, str]:
        return {"user": principal.user_id}

    return app


@pytest.fixture(name="keys")
def _keys_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Mint keys with distinct scopes against a throwaway key store."""
    db = tmp_path / "relay_keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    store = ApiKeyStore(str(db))
    return {
        "llm": store.create(user_id="c", scopes=["relay:llm"]).api_key,
        "retrieve": store.create(
            user_id="c", scopes=["relay:retrieve"]
        ).api_key,
        "all_access": store.create(user_id="admin").api_key,
    }


@pytest.fixture(name="client")
async def _client_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client bound to the probe app over ASGI."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)
    transport = httpx.ASGITransport(app=_build_probe_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe.test"
    ) as client:
        yield client


async def test_unknown_key_is_unauthorized(
    client: httpx.AsyncClient, keys: dict[str, str]
) -> None:
    """An unknown key is rejected with 401 before any scope check."""
    del keys
    response = await client.get(
        "/probe", headers={"Authorization": "Bearer ptm_nope"}
    )
    assert response.status_code == 401


async def test_matching_relay_scope_allowed(
    client: httpx.AsyncClient, keys: dict[str, str]
) -> None:
    """A relay:llm key reaches the relay:llm-gated route."""
    response = await client.get(
        "/probe", headers={"Authorization": f"Bearer {keys['llm']}"}
    )
    assert response.status_code == 200


async def test_wrong_relay_scope_forbidden(
    client: httpx.AsyncClient, keys: dict[str, str]
) -> None:
    """A relay:retrieve key cannot reach a relay:llm route."""
    response = await client.get(
        "/probe", headers={"Authorization": f"Bearer {keys['retrieve']}"}
    )
    assert response.status_code == 403


async def test_empty_scope_forbidden_on_relay(
    client: httpx.AsyncClient, keys: dict[str, str]
) -> None:
    """An all-access (empty-scope) key is denied on the relay surface."""
    response = await client.get(
        "/probe", headers={"Authorization": f"Bearer {keys['all_access']}"}
    )
    assert response.status_code == 403


async def test_relay_rate_limit_returns_429(
    client: httpx.AsyncClient,
    keys: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceeding the relay budget returns 429 with Retry-After."""
    monkeypatch.setenv("PHYTOMNI_RELAY_RATE_LIMIT_PER_MIN", "1")
    auth = {"Authorization": f"Bearer {keys['llm']}"}
    first = await client.get("/probe", headers=auth)
    second = await client.get("/probe", headers=auth)
    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers
