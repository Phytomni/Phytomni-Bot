# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the /v1/api-keys management routes.

Covers the service-token gating contract (503/401), the one-time
plaintext on POST, listing with/without user_id filter, and the
revoke endpoint's deleted=True/False semantics.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mcp_server_phytomni.api.auth import ApiKeyStore

pytestmark = pytest.mark.server


@pytest.fixture(name="service_token")
def _service_token_fixture(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a service token for the duration of one test."""
    token = "svc-test-token-xyz"
    monkeypatch.setenv("API_SERVICE_TOKEN", token)
    return token


@pytest.fixture(name="admin_keys_db")
def _admin_keys_db_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the key store at a throwaway SQLite DB."""
    db = tmp_path / "admin_keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    return db


async def test_post_returns_503_when_service_token_unconfigured(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a fresh deployment with no service token blocks issuance."""
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)

    response = await api_client.post(
        "/v1/api-keys",
        headers={"Authorization": "Bearer any-token"},
        json={"user_id": "alice@example.com"},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "unavailable"
    assert error["message"] == "service unavailable"
    assert error["retryable"] is False
    assert "not enabled" not in error["message"]


async def test_post_returns_401_when_wrong_service_token(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify a wrong service token cannot mint keys."""
    del service_token, admin_keys_db
    response = await api_client.post(
        "/v1/api-keys",
        headers={"Authorization": "Bearer wrong-token"},
        json={"user_id": "alice@example.com"},
    )

    assert response.status_code == 401


async def test_post_mints_user_key_with_correct_service_token(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify the happy-path mint returns plaintext key once."""
    response = await api_client.post(
        "/v1/api-keys",
        headers={"Authorization": f"Bearer {service_token}"},
        json={"user_id": "alice@example.com", "name": "chat-ai"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["object"] == "api_key"
    assert body["api_key"].startswith("ptm_")
    assert body["user_id"] == "alice@example.com"
    assert body["expires_at"] is None
    assert body["prefix"] == body["api_key"][:12]
    # The minted key must be usable for normal auth flows.
    record = ApiKeyStore(str(admin_keys_db)).list(user_id="alice@example.com")
    assert len(record) == 1


async def test_post_honours_expires_days(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify expires_days produces an ISO-8601 expires_at timestamp."""
    del admin_keys_db
    response = await api_client.post(
        "/v1/api-keys",
        headers={"Authorization": f"Bearer {service_token}"},
        json={"user_id": "bob", "expires_days": 30},
    )

    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


async def test_get_lists_keys_filtered_by_user(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify GET returns metadata-only rows and ?user_id= filters."""
    store = ApiKeyStore(str(admin_keys_db))
    store.create(user_id="alice", name="alice-laptop")
    store.create(user_id="bob", name="bob-laptop")

    list_all = await api_client.get(
        "/v1/api-keys",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert list_all.status_code == 200
    assert len(list_all.json()["data"]) == 2

    list_alice = await api_client.get(
        "/v1/api-keys?user_id=alice",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    assert list_alice.status_code == 200
    body = list_alice.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["user_id"] == "alice"
    assert body["data"][0]["active"] is True
    # Metadata-only: no hash / salt / plaintext leak.
    assert "api_key" not in body["data"][0]
    assert "salt" not in body["data"][0]
    assert "key_hash" not in body["data"][0]


async def test_delete_revokes_existing_prefix(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify DELETE flips the active flag on an active key."""
    created = ApiKeyStore(str(admin_keys_db)).create(user_id="alice")

    response = await api_client.delete(
        f"/v1/api-keys/{created.prefix}",
        headers={"Authorization": f"Bearer {service_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "api_key.deleted"
    assert body["prefix"] == created.prefix
    assert body["deleted"] is True


async def test_delete_returns_false_for_unknown_prefix(
    api_client: httpx.AsyncClient,
    service_token: str,
    admin_keys_db: Path,
) -> None:
    """Verify DELETE on a non-existent prefix returns deleted=False."""
    del admin_keys_db
    response = await api_client.delete(
        "/v1/api-keys/ptm_unknown",
        headers={"Authorization": f"Bearer {service_token}"},
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is False


async def test_get_requires_service_token(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify GET also enforces the service-token gate."""
    response = await api_client.get("/v1/api-keys")

    # 503 when API_SERVICE_TOKEN is unset in the test env.
    assert response.status_code in {503, 401}
