# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the /v1/relay/audit admin query routes.

Covers service-token gating (503/401), user-key rejection, filtered
listing, request-id lookup, and the no-secret-leak response contract.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mcp_server_phytomni.api.relay.audit import (
    RelayAuditRecord,
    RelayAuditStore,
)

pytestmark = pytest.mark.server


@pytest.fixture(name="service_token")
def _service_token_fixture(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a service token for the duration of one test."""
    token = "svc-test-token-audit"
    monkeypatch.setenv("API_SERVICE_TOKEN", token)
    return token


@pytest.fixture(name="audit_db")
def _audit_db_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the relay audit store at a throwaway SQLite DB and seed it."""
    db = tmp_path / "relay_audit.sqlite"
    monkeypatch.setenv("PHYTOMNI_RELAY_AUDIT_DB_PATH", str(db))
    store = RelayAuditStore(str(db))
    store.record(
        RelayAuditRecord(
            request_id="req-a",
            user_id="alice",
            key_prefix="ptm_alice01",
            service="llm",
            status_code=200,
            request_body='{"prompt":"hello","api_key":"ptm-secret"}',
            response_body="world",
        )
    )
    store.record(
        RelayAuditRecord(
            request_id="req-b",
            user_id="bob",
            key_prefix="ptm_bob0001",
            service="retrieve",
            status_code=500,
        )
    )
    return db


async def test_list_returns_503_when_service_token_unconfigured(
    api_client: httpx.AsyncClient,
    audit_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment with no service token blocks audit reads."""
    del audit_db
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)

    response = await api_client.get(
        "/v1/relay/audit", headers={"Authorization": "Bearer anything"}
    )

    assert response.status_code == 503


async def test_list_returns_401_with_wrong_service_token(
    api_client: httpx.AsyncClient,
    service_token: str,
    audit_db: Path,
) -> None:
    """A wrong service token cannot read the audit trail."""
    del service_token, audit_db
    response = await api_client.get(
        "/v1/relay/audit", headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401


async def test_list_returns_401_for_user_key_alone(
    api_client: httpx.AsyncClient,
    service_token: str,
    audit_db: Path,
) -> None:
    """A per-user ptm_ key alone cannot query audit records."""
    del service_token, audit_db
    response = await api_client.get(
        "/v1/relay/audit",
        headers={"Authorization": "Bearer ptm_some_user_key"},
    )

    assert response.status_code == 401


async def test_list_with_service_token_returns_records(
    api_client: httpx.AsyncClient,
    service_token: str,
    audit_db: Path,
) -> None:
    """The service token lists audit records without leaking secrets."""
    del audit_db
    response = await api_client.get(
        "/v1/relay/audit",
        headers={"Authorization": f"Bearer {service_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    ids = {record["request_id"] for record in body["data"]}
    assert ids == {"req-a", "req-b"}
    for record in body["data"]:
        assert "key_prefix" in record
        assert not {"key_hash", "salt", "api_key"} & set(record)


async def test_list_filters_by_service(
    api_client: httpx.AsyncClient,
    service_token: str,
    audit_db: Path,
) -> None:
    """The service filter narrows the listing."""
    del audit_db
    response = await api_client.get(
        "/v1/relay/audit",
        params={"service": "llm"},
        headers={"Authorization": f"Bearer {service_token}"},
    )

    assert response.status_code == 200
    ids = {record["request_id"] for record in response.json()["data"]}
    assert ids == {"req-a"}


async def test_get_by_request_id_returns_match(
    api_client: httpx.AsyncClient,
    service_token: str,
    audit_db: Path,
) -> None:
    """Fetching by request id returns that request's records."""
    del audit_db
    response = await api_client.get(
        "/v1/relay/audit/req-a",
        headers={"Authorization": f"Bearer {service_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-a"
    assert len(body["data"]) == 1
    assert body["data"][0]["user_id"] == "alice"
    request_body = body["data"][0]["request_body"]
    assert request_body is not None
    assert request_body == '{"prompt":"hello","api_key":"[REDACTED]"}'
    assert "ptm-secret" not in request_body
