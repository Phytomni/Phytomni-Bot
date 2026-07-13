# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP contract tests for the opt-in user-scoped memory CRUD surface."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore

pytestmark = pytest.mark.server

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
type MemoryBundle = tuple[httpx.AsyncClient, str, str]


@pytest.fixture(name="memory_bundle")
async def _memory_bundle(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MemoryBundle]:
    """Build an enabled app with two isolated authenticated users."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_MEMORY_DB_PATH", str(tmp_path / "memories.sqlite")
    )
    key_db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", key_db)
    key_store = ApiKeyStore(key_db)
    alice = key_store.create(user_id="alice").api_key
    bob = key_store.create(user_id="bob").api_key
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test"
    ) as client:
        yield client, alice, bob


@pytest.fixture(name="disabled_memory_client")
async def _disabled_memory_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Build the default-off app and verify it never mounts memory routes."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "0")
    monkeypatch.setenv(
        "PHYTOMNI_MEMORY_DB_PATH", str(tmp_path / "memories.sqlite")
    )
    key_db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", key_db)
    key = ApiKeyStore(key_db).create(user_id="alice").api_key
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test"
    ) as client:
        yield client, key


def _auth(key: str) -> dict[str, str]:
    """Return the standard bearer header for one test key."""
    return {"Authorization": f"Bearer {key}"}


async def test_memory_routes_are_hidden_when_disabled(
    disabled_memory_client: tuple[httpx.AsyncClient, str],
) -> None:
    """The feature flag removes the route instead of returning a soft 403."""
    client, key = disabled_memory_client

    response = await client.get("/v1/memories", headers=_auth(key))

    assert response.status_code == 404


async def test_memory_create_list_and_namespace_isolation(
    memory_bundle: MemoryBundle,
) -> None:
    """Bodies cannot choose an owner and reads stay user-scoped."""
    client, alice, bob = memory_bundle
    body = {"kind": "preference", "content": "prefers Arabidopsis"}

    forged = await client.post(
        "/v1/memories",
        json={**body, "user_id": "bob"},
        headers=_auth(alice),
    )
    assert forged.status_code == 422

    created = await client.post(
        "/v1/memories", json=body, headers=_auth(alice)
    )
    assert created.status_code == 201
    record = created.json()
    assert record["user_id"] == "alice"
    assert record["revision"] == 1

    listed = await client.get("/v1/memories", headers=_auth(alice))
    assert [item["id"] for item in listed.json()["data"]] == [record["id"]]
    assert (
        await client.get(f"/v1/memories/{record['id']}", headers=_auth(bob))
    ).status_code == 404
    assert (await client.get("/v1/memories", headers=_auth(bob))).json()[
        "data"
    ] == []


async def test_memory_update_requires_revision_and_rejects_stale_write(
    memory_bundle: MemoryBundle,
) -> None:
    """PUT uses a positive ``If-Match`` revision as its concurrency guard."""
    client, alice, _bob = memory_bundle
    created = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "first"},
        headers=_auth(alice),
    )
    memory_id = created.json()["id"]
    update_url = f"/v1/memories/{memory_id}"
    update_body = {"kind": "note", "content": "second"}

    missing = await client.put(
        update_url, json=update_body, headers=_auth(alice)
    )
    assert missing.status_code == 428
    invalid = await client.put(
        update_url,
        json=update_body,
        headers={**_auth(alice), "If-Match": "bogus"},
    )
    assert invalid.status_code == 400

    updated = await client.put(
        update_url,
        json=update_body,
        headers={**_auth(alice), "If-Match": '"1"'},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    stale = await client.put(
        update_url,
        json={"kind": "note", "content": "stale"},
        headers={**_auth(alice), "If-Match": "1"},
    )
    assert stale.status_code == 409


async def test_memory_delete_is_idempotent_and_owner_scoped(
    memory_bundle: MemoryBundle,
) -> None:
    """A foreign or repeated delete reveals no record and never succeeds."""
    client, alice, bob = memory_bundle
    created = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "remove me"},
        headers=_auth(alice),
    )
    memory_id = created.json()["id"]
    delete_url = f"/v1/memories/{memory_id}"

    foreign = await client.delete(delete_url, headers=_auth(bob))
    assert foreign.status_code == 200
    assert foreign.json()["deleted"] is False
    deleted = await client.delete(
        delete_url,
        headers={**_auth(alice), "If-Match": "1"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    repeated = await client.delete(delete_url, headers=_auth(alice))
    assert repeated.status_code == 200
    assert repeated.json()["deleted"] is False
