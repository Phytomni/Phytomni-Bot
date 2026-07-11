# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the flag-gated A2A v1 HTTP boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore

pytestmark = pytest.mark.server
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


@pytest.fixture(name="client_bundle")
async def build_a2a_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Build an enabled A2A app and a scoped API key."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    monkeypatch.setenv("PHYTOMNI_A2A_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://public.example/base"
    )
    db = str(tmp_path / "a2a-keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    key = ApiKeyStore(db).create(user_id="a2a-user", scopes=["agents"]).api_key
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://api.test",
    )
    try:
        yield client, key
    finally:
        await client.aclose()


async def test_agent_card_is_public_and_flag_gated(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Enabled discovery returns a card without requiring an API key."""
    client, _key = client_bundle

    response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    body = response.json()
    assert body["supportedInterfaces"][0]["url"] == (
        "https://public.example/base/a2a"
    )
    assert body["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert body["capabilities"]["streaming"] is False


async def test_a2a_requires_auth_and_exact_protocol_header(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Transport auth and version checks happen before JSON-RPC dispatch."""
    client, key = client_bundle
    payload = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {}}

    unauthorized = await client.post(
        "/a2a",
        json=payload,
        headers={"A2A-Version": "1.0"},
    )
    assert unauthorized.status_code == 401

    bad_version = await client.post(
        "/a2a",
        json=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "0.3",
        },
    )
    assert bad_version.status_code == 400
    assert "A2A-Version" in bad_version.json()["error"]["message"]


async def test_a2a_business_errors_stay_jsonrpc_http_200(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Unsupported methods are protocol errors, not transport failures."""
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "unsupported",
            "method": "GetTask",
            "params": {},
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "unsupported"
    assert body["error"]["code"] == -32004


async def test_a2a_unknown_skill_is_invalid_params_error(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Unknown request-level skill ids never reach the agent invoker."""
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "SendMessage",
            "params": {
                "metadata": {"skill_id": "MissingAgent"},
                "message": {
                    "messageId": "m1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602
