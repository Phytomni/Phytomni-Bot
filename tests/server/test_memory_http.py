# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP contract tests for the opt-in user-scoped memory CRUD surface."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.routes import memory
from mcp_server_phytomni.api.schemas import MemoryResponse
from mcp_server_phytomni.runtime.memory import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryPolicyError,
    MemoryStore,
    MemoryStoreError,
)

pytestmark = pytest.mark.server

type MemoryBundle = tuple[httpx.AsyncClient, str, str]
type DisabledMemoryBundle = tuple[httpx.AsyncClient, str, Path]


@pytest.fixture(name="memory_bundle")
async def _memory_bundle(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MemoryBundle]:
    """Build an enabled app with two isolated authenticated users."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_MEMORY_DB_PATH", str(tmp_path / "memories.sqlite")
    )
    monkeypatch.setenv("PHYTOMNI_API_SERVICE_TOKEN", "audit-service-token")
    key_db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", key_db)
    key_store = ApiKeyStore(key_db)
    alice = key_store.create(user_id="alice").api_key
    bob = key_store.create(user_id="bob").api_key
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.test"
    ) as client:
        yield client, alice, bob


@pytest.fixture(name="disabled_memory_client")
async def _disabled_memory_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[DisabledMemoryBundle]:
    """Build the default-off app and verify it never mounts memory routes."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "0")
    memory_path = tmp_path / "memories.sqlite"
    monkeypatch.setenv("PHYTOMNI_MEMORY_DB_PATH", str(memory_path))
    key_db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", key_db)
    key = ApiKeyStore(key_db).create(user_id="alice").api_key
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.test"
    ) as client:
        yield client, key, memory_path


def _auth(key: str) -> dict[str, str]:
    """Return the standard bearer header for one test key."""
    return {"Authorization": f"Bearer {key}"}


async def test_memory_routes_are_hidden_when_disabled(
    disabled_memory_client: DisabledMemoryBundle,
) -> None:
    """The feature flag removes the route instead of returning a soft 403."""
    client, key, memory_path = disabled_memory_client

    response = await client.get("/v1/memories", headers=_auth(key))

    assert response.status_code == 404
    assert not memory_path.exists()


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


async def test_memory_export_is_live_and_owner_scoped(
    memory_bundle: MemoryBundle,
) -> None:
    """Export returns only the authenticated user's explicit memories."""
    client, alice, bob = memory_bundle
    alice_first = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "alice first"},
        headers=_auth(alice),
    )
    alice_second = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "alice second"},
        headers=_auth(alice),
    )
    bob_record = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "bob only"},
        headers=_auth(bob),
    )

    exported = await client.get("/v1/memories/export", headers=_auth(alice))
    assert exported.status_code == 200
    assert exported.json()["object"] == "memory.export"
    assert [item["id"] for item in exported.json()["data"]] == [
        alice_second.json()["id"],
        alice_first.json()["id"],
    ]
    assert bob_record.json()["id"] not in {
        item["id"] for item in exported.json()["data"]
    }


async def test_memory_store_failure_is_fail_closed_and_redacted(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken DB returns generic 503s without exposing its path."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "1")
    memory_path = tmp_path / "secret-memory.sqlite"
    memory_path.mkdir()
    monkeypatch.setenv("PHYTOMNI_MEMORY_DB_PATH", str(memory_path))
    key_db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", key_db)
    key = ApiKeyStore(key_db).create(user_id="alice").api_key
    caplog.set_level("WARNING")

    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.test"
    ) as client:
        write = await client.post(
            "/v1/memories",
            json={"kind": "note", "content": "private"},
            headers=_auth(key),
        )
        read = await client.get("/v1/memories", headers=_auth(key))

    assert write.status_code == 503
    assert read.status_code == 503
    assert "secret-memory.sqlite" not in write.text
    assert "secret-memory.sqlite" not in read.text
    assert "secret-memory.sqlite" not in caplog.text


async def test_memory_audit_is_service_gated_and_digest_only(
    memory_bundle: MemoryBundle,
) -> None:
    """The admin view exposes mutation metadata but never memory content."""
    client, alice, _bob = memory_bundle
    created = await client.post(
        "/v1/memories",
        json={"kind": "note", "content": "secret-ish text"},
        headers=_auth(alice),
    )
    memory_id = created.json()["id"]
    await client.put(
        f"/v1/memories/{memory_id}",
        json={"kind": "note", "content": "changed text"},
        headers={**_auth(alice), "If-Match": "1"},
    )
    await client.delete(
        f"/v1/memories/{memory_id}",
        headers={**_auth(alice), "If-Match": "2"},
    )

    assert (await client.get("/v1/memories/audit")).status_code == 401
    denied = await client.get(
        "/v1/memories/audit",
        headers={"X-Service-Token": "wrong"},
    )
    assert denied.status_code == 401
    response = await client.get(
        "/v1/memories/audit",
        headers={"X-Service-Token": "audit-service-token"},
    )
    assert response.status_code == 200
    records = response.json()["data"]
    assert [item["operation"] for item in records] == [
        "delete",
        "update",
        "create",
    ]
    assert all(item["user_id"] == "alice" for item in records)
    assert all(item["request_id"] for item in records)
    assert all("content" not in item for item in records)
    assert all(
        len(item["before_digest"] or item["after_digest"]) == 64
        for item in records
    )

    filtered = await client.get(
        "/v1/memories/audit?operation=update&limit=1",
        headers={"X-Service-Token": "audit-service-token"},
    )
    assert [item["operation"] for item in filtered.json()["data"]] == [
        "update"
    ]


def _default_memory_stamps() -> dict[str, datetime | None]:
    """Return the default created/updated/expires stamps."""
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    return {"created_at": stamp, "updated_at": stamp, "expires_at": None}


@dataclass
class _FakeMemoryRecord:
    """Minimal memory record accepted by the public response model."""

    id: str = "mem-1"
    user_id: str = "alice"
    kind: str = "note"
    content: str = "hello"
    tags: list[str] = field(default_factory=list)
    stamps: dict[str, datetime | None] = field(
        default_factory=_default_memory_stamps
    )
    revision: int = 1

    def model_dump(self) -> dict[str, object]:
        """Return the public memory payload."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind,
            "content": self.content,
            "tags": self.tags,
            "created_at": self.stamps["created_at"],
            "updated_at": self.stamps["updated_at"],
            "expires_at": self.stamps["expires_at"],
            "revision": self.revision,
        }


class _ConfigurableMemoryStore:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def create(self, _write: object) -> _FakeMemoryRecord:
        """Return one fake record after optional injected failure."""
        self._raise()
        return _FakeMemoryRecord()

    def list(
        self, _owner: str, **_kwargs: object
    ) -> Sequence[_FakeMemoryRecord]:
        """Return an empty list after optional injected failure."""
        self._raise()
        return []

    def export(self, _owner: str) -> Sequence[_FakeMemoryRecord]:
        """Return an empty export after optional injected failure."""
        self._raise()
        return []

    def list_audit(self, **_kwargs: object) -> Sequence[object]:
        """Return an empty audit list after optional injected failure."""
        self._raise()
        return []

    def get(self, _owner: str, _memory_id: str) -> _FakeMemoryRecord | None:
        """Return one fake record after optional injected failure."""
        self._raise()
        return _FakeMemoryRecord()

    def update(self, *_args: object, **_kwargs: object) -> _FakeMemoryRecord:
        """Return a bumped-revision record after optional injected failure."""
        self._raise()
        return _FakeMemoryRecord(revision=2)

    def delete(self, *_args: object, **_kwargs: object) -> bool:
        """Report a successful delete after optional injected failure."""
        self._raise()
        return True


def _isolated_memory_app(
    *, store: object, current_user: str | None = "alice"
) -> FastAPI:
    def _revision(value: str | None, *, required: bool) -> int | None:
        if value is None or not str(value).strip():
            return 1 if required else None
        return int(str(value).strip().strip('"'))

    app = FastAPI()
    memory.register_memory_routes(
        app,
        memory.MemoryRouteDependencies(
            get_store=lambda: cast(MemoryStore, store),
            auth=memory.MemoryAuthDependencies(
                require_agents=lambda: None, require_service=lambda: None
            ),
            context=memory.MemoryContextDependencies(
                current_user=lambda: current_user,
                current_request_id=lambda: "rid",
            ),
            projection=memory.MemoryProjectionDependencies(
                memory_write=lambda _o, payload: payload,
                memory_response=lambda record: MemoryResponse.model_validate(
                    record.model_dump()
                ),
                memory_audit_response=lambda record: record,
                memory_revision=_revision,
            ),
        ),
    )
    return app


async def test_isolated_memory_routes_map_store_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each memory route maps policy, validation, and store faults."""
    cases = (
        ("POST", "/v1/memories", MemoryPolicyError("too large"), 413),
        ("POST", "/v1/memories", ValueError("bad write"), 400),
        ("POST", "/v1/memories", MemoryStoreError("down"), 503),
        ("GET", "/v1/memories", MemoryPolicyError("bad limit"), 400),
        ("GET", "/v1/memories", ValueError("bad query"), 400),
        ("GET", "/v1/memories", sqlite3.OperationalError("locked"), 503),
        ("GET", "/v1/memories/export", ValueError("bad export"), 400),
        ("GET", "/v1/memories/export", OSError("export unavailable"), 503),
        ("GET", "/v1/memories/audit", MemoryPolicyError("bad audit"), 400),
        ("GET", "/v1/memories/audit", MemoryStoreError("audit down"), 503),
        ("GET", "/v1/memories/mem-1", ValueError("bad id"), 400),
        ("GET", "/v1/memories/mem-1", MemoryStoreError("get down"), 503),
        ("PUT", "/v1/memories/mem-1", MemoryNotFoundError("gone"), 404),
        ("PUT", "/v1/memories/mem-1", MemoryConflictError("stale"), 409),
        ("PUT", "/v1/memories/mem-1", MemoryPolicyError("huge"), 413),
        ("PUT", "/v1/memories/mem-1", ValueError("bad update"), 400),
        ("PUT", "/v1/memories/mem-1", MemoryStoreError("put down"), 503),
        ("DELETE", "/v1/memories/mem-1", MemoryConflictError("stale"), 409),
        ("DELETE", "/v1/memories/mem-1", ValueError("bad delete"), 400),
        (
            "DELETE",
            "/v1/memories/mem-1",
            sqlite3.DatabaseError("delete down"),
            503,
        ),
    )
    body = {"kind": "note", "content": "payload"}
    for method, path, error, status in cases:
        app = _isolated_memory_app(store=_ConfigurableMemoryStore(error))
        async with open_asgi_client(
            monkeypatch, app, base_url="http://memory.test"
        ) as client:
            headers = (
                {"If-Match": "1"} if method in {"PUT", "DELETE"} else None
            )
            response = await client.request(
                method,
                path,
                json=body if method in {"POST", "PUT"} else None,
                headers=headers,
            )
        assert response.status_code == status, (method, path, error)


async def test_isolated_memory_owner_and_missing_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing user context fails closed and missing rows stay 404."""
    async with open_asgi_client(
        monkeypatch,
        _isolated_memory_app(
            store=_ConfigurableMemoryStore(), current_user=None
        ),
        base_url="http://memory.test",
    ) as client:
        assert (await client.get("/v1/memories")).status_code == 401

    class _MissingStore(_ConfigurableMemoryStore):
        def get(self, _owner: str, _memory_id: str) -> None:
            return None

    async with open_asgi_client(
        monkeypatch,
        _isolated_memory_app(store=_MissingStore()),
        base_url="http://memory.test",
    ) as client:
        assert (
            await client.get("/v1/memories/mem-missing")
        ).status_code == 404
