# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP contracts for the breaking resumable upload resource."""

from __future__ import annotations

from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.resumable_uploads import (
    ResumableUploadService,
    UploadServiceConfig,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.resumable_uploads import (
    ResumableUploadRegistry,
)
from mcp_server_phytomni.storage.multipart import FakeMultipartStorage

pytestmark = pytest.mark.server


@pytest.fixture(name="resumable_upload_client")
async def _resumable_upload_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, str, FakeMultipartStorage]]:
    """Build an API client with an injectable local upload service."""
    keys_path = str(tmp_path / "keys.sqlite")
    uploads_path = str(tmp_path / "uploads.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", keys_path)
    monkeypatch.setenv(
        "PHYTOMNI_API_UPLOAD_V2_ALLOWED_ORIGINS",
        '["https://web.example"]',
    )
    key_store = ApiKeyStore(keys_path)
    control_key = key_store.create(
        user_id="web-service",
        scopes=("files:delegate",),
    ).api_key
    ordinary_key = key_store.create(user_id="ordinary-user").api_key
    storage = FakeMultipartStorage()
    service = ResumableUploadService(
        ResumableUploadRegistry(uploads_path),
        storage,
        UploadServiceConfig(
            bucket_name="test-bucket",
            upload_origin="https://upload.example",
        ),
    )
    monkeypatch.setattr(
        UploadRuntime,
        "get_upload_service",
        lambda _runtime: service,
    )
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="https://api.test"
    ) as client:
        yield client, control_key, ordinary_key, storage


def _control_headers(key: str) -> dict[str, str]:
    """Return the trusted Web control-plane authorization header."""
    return {"Authorization": f"Bearer {key}"}


def _data_headers(capability: str) -> dict[str, str]:
    """Return the browser capability header only."""
    return {"Authorization": f"Bearer {capability}"}


async def _create(
    client: httpx.AsyncClient,
    control_key: str,
    *,
    owner: str = "alice@example.com",
    idempotency_key: str = "upload-route-1",
    purpose: str = "chat_attachment",
) -> httpx.Response:
    """Create one small synthetic asset through the Web control shape."""
    return await client.post(
        "/v1/files",
        headers=_control_headers(control_key),
        json={
            "owner_subject": owner,
            "filename": "sample.fastq.gz",
            "size_bytes": 3,
            "content_type_hint": "application/gzip",
            "last_modified_ms": 1722470400000,
            "purpose": purpose,
            "idempotency_key": idempotency_key,
        },
    )


async def test_invalid_purpose_has_stable_validation_error(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage
    ],
) -> None:
    """Reject unsupported purposes before opening a provider session."""
    client, control_key, _ordinary_key, storage = resumable_upload_client
    response = await _create(
        client,
        control_key,
        owner="owner-with-purpose",
        purpose="Dataset",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_purpose_invalid"
    assert response.json()["error"]["stage"] == "request_validation"
    assert response.json()["error"]["retryable"] is False
    assert not storage.sessions
    for value in ("Dataset", "owner-with-purpose", "test-bucket"):
        assert value not in response.text
    assert "object_key" not in response.text


async def test_multipart_route_is_rejected_and_control_scope_is_explicit(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage
    ],
) -> None:
    """Old multipart input cannot reach storage and scope-less keys fail."""
    client, control_key, ordinary_key, storage = resumable_upload_client
    rejected = await client.post(
        "/v1/files",
        headers=_control_headers(control_key),
        files={"file": ("sample.txt", b"abc", "text/plain")},
    )
    assert rejected.status_code == 422
    assert not storage.sessions

    forbidden = await _create(client, ordinary_key)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"


async def test_resumable_data_plane_streams_parts_and_completes(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage
    ],
) -> None:
    """Create, HEAD, PUT, complete, and abort use the split resource API."""
    client, control_key, _ordinary_key, storage = resumable_upload_client
    created = await _create(client, control_key)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    session = created.json()
    assert session["protocol"] == "obs-multipart-v2"
    assert session["upload_url"] == (
        f"https://upload.example/v1/files/{session['asset_id']}"
    )
    assert "object_key" not in created.text
    capability = session["capability"]

    head = await client.head(
        f"/v1/files/{session['asset_id']}",
        headers=_data_headers(capability),
    )
    assert head.status_code == 200
    assert head.headers["upload-protocol"] == "obs-multipart-v2"
    assert head.headers["upload-status"] == "uploading"
    assert head.headers["upload-length"] == "3"
    assert head.headers["upload-part-count"] == "1"
    assert head.headers["upload-received-parts"] == ""
    assert head.headers["cache-control"] == "no-store"
    assert head.headers["x-request-id"]

    body = b"abc"
    part = await client.put(
        f"/v1/files/{session['asset_id']}/parts/1",
        headers={
            **_data_headers(capability),
            "Content-Type": "application/octet-stream",
            "X-Phytomni-Part-SHA256": sha256(body).hexdigest(),
        },
        content=body,
    )
    assert part.status_code == 200
    assert part.json()["received_parts"] == [1]
    assert storage.read_sizes == [3]

    complete = await client.post(
        f"/v1/files/{session['asset_id']}/complete",
        headers=_data_headers(capability),
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"
    assert complete.json()["completed_at"]
    assert complete.headers["cache-control"] == "no-store"

    revoked = await client.head(
        f"/v1/files/{session['asset_id']}",
        headers=_data_headers(capability),
    )
    assert revoked.status_code == 401
    assert revoked.headers["cache-control"] == "no-store"


async def test_capabilities_are_asset_scoped_and_checksum_errors_are_stable(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage
    ],
) -> None:
    """A bearer for one asset cannot inspect or write another asset."""
    client, control_key, _ordinary_key, _storage = resumable_upload_client
    first = await _create(
        client, control_key, idempotency_key="upload-route-a"
    )
    second = await _create(
        client,
        control_key,
        idempotency_key="upload-route-b",
        owner="bob@example.com",
    )
    first_body = first.json()
    second_body = second.json()
    cross_asset = await client.head(
        f"/v1/files/{second_body['asset_id']}",
        headers=_data_headers(first_body["capability"]),
    )
    assert cross_asset.status_code == 401
    assert cross_asset.content == b""
    assert cross_asset.headers["cache-control"] == "no-store"

    body = b"abc"
    checksum_error = await client.put(
        f"/v1/files/{first_body['asset_id']}/parts/1",
        headers={
            **_data_headers(first_body["capability"]),
            "X-Phytomni-Part-SHA256": "0" * 64,
        },
        content=body,
    )
    assert checksum_error.status_code == 422
    assert checksum_error.json()["error"]["code"] == (
        "upload_checksum_mismatch"
    )


async def test_cors_allows_configured_origin_without_credentials(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage
    ],
) -> None:
    """The direct browser data plane exposes only the configured origin."""
    client, _control_key, _ordinary_key, _storage = resumable_upload_client
    response = await client.options(
        "/v1/files/file_missing",
        headers={
            "Origin": "https://web.example",
            "Access-Control-Request-Method": "HEAD",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://web.example"
    )
    assert response.headers.get("access-control-allow-credentials") != "true"

    denied = await client.options(
        "/v1/files/file_missing",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "HEAD",
        },
    )
    assert "access-control-allow-origin" not in denied.headers
