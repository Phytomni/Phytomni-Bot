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
from mcp_server_phytomni.api.schemas import AssetDescriptor
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
) -> AsyncIterator[
    tuple[httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]]
]:
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
    cleanup_triggers: list[None] = []
    monkeypatch.setattr(
        UploadRuntime,
        "trigger_cleanup",
        lambda _runtime: cleanup_triggers.append(None),
        raising=False,
    )
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="https://api.test"
    ) as client:
        yield client, control_key, ordinary_key, storage, cleanup_triggers


def _control_headers(key: str) -> dict[str, str]:
    """Return the trusted Web control-plane authorization header."""
    return {"Authorization": f"Bearer {key}"}


def _data_headers(capability: str) -> dict[str, str]:
    """Return the browser capability header only."""
    return {"Authorization": f"Bearer {capability}"}


def _assert_cleanup_triggered(
    response: httpx.Response,
    expected_status: int,
    cleanup: list[None],
    previous_count: int,
) -> int:
    """Assert one request finalized exactly one cleanup dependency."""
    assert response.status_code == expected_status
    assert len(cleanup) == previous_count + 1
    return len(cleanup)


async def _create(
    client: httpx.AsyncClient,
    control_key: str,
    *,
    owner: str = "alice@example.com",
    idempotency_key: str = "upload-route-1",
    purpose: str | None = "document",
) -> httpx.Response:
    """Create one small synthetic asset through the Web control shape."""
    payload: dict[str, object] = {
        "owner_subject": owner,
        "filename": "sample.fastq.gz",
        "size_bytes": 3,
        "content_type": "application/gzip",
        "last_modified_ms": 1722470400000,
        "idempotency_key": idempotency_key,
    }
    if purpose is not None:
        payload["purpose"] = purpose
    return await client.post(
        "/v1/files",
        headers=_control_headers(control_key),
        json=payload,
    )


async def test_invalid_purpose_has_stable_validation_error(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]
    ],
) -> None:
    """Reject unsupported purposes before opening a provider session."""
    client, control_key, _ordinary_key, storage, _cleanup = (
        resumable_upload_client
    )
    for purpose in ("dataset", "document"):
        valid = await _create(
            client,
            control_key,
            owner="owner-with-purpose",
            idempotency_key=f"valid-{purpose}",
            purpose=purpose,
        )
        assert valid.status_code == 201
    expected_object_key = next(
        iter(storage.sessions.values())
    ).session.object_key
    session_count = len(storage.sessions)
    for index, value in enumerate(
        (None, "chat_attachment", "unknown", "Document"), start=1
    ):
        response = await _create(
            client,
            control_key,
            owner="owner-with-purpose",
            idempotency_key=f"invalid-purpose-{index}",
            purpose=value,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == (
            "attachment_purpose_invalid"
        )
        assert response.json()["error"]["stage"] == "request_validation"
        assert response.json()["error"]["retryable"] is False
        assert len(storage.sessions) == session_count
        for marker in ("owner-with-purpose", "test-bucket"):
            assert marker not in response.text
        if value is not None:
            assert value not in response.text
        assert expected_object_key not in response.text
        assert "object_key" not in response.text


async def test_multipart_route_is_rejected_and_control_scope_is_explicit(
    resumable_upload_client: tuple[
        httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]
    ],
) -> None:
    """Old multipart input cannot reach storage and scope-less keys fail."""
    client, control_key, ordinary_key, storage, _cleanup = (
        resumable_upload_client
    )
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
        httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]
    ],
) -> None:
    """Create, HEAD, PUT, complete, and abort use the split resource API."""
    client, control_key, _ordinary_key, storage, _cleanup = (
        resumable_upload_client
    )
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
    assert set(complete.json()) == set(AssetDescriptor.model_fields)
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
        httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]
    ],
) -> None:
    """A bearer for one asset cannot inspect or write another asset."""
    client, control_key, _ordinary_key, _storage, _cleanup = (
        resumable_upload_client
    )
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
        httpx.AsyncClient, str, str, FakeMultipartStorage, list[None]
    ],
) -> None:
    """The direct browser data plane exposes only the configured origin."""
    client, _control_key, _ordinary_key, _storage, _cleanup = (
        resumable_upload_client
    )
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


async def test_every_upload_route_finalizes_cleanup_after_success_or_error(
    resumable_upload_client: tuple[
        httpx.AsyncClient,
        str,
        str,
        FakeMultipartStorage,
        list[None],
    ],
) -> None:
    """Every upload route triggers cleanup after success and stable errors."""
    client, control_key, _ordinary_key, _storage, cleanup = (
        resumable_upload_client
    )

    cleanup_count = 0

    created = await _create(client, control_key, idempotency_key="route-final")
    cleanup_count = _assert_cleanup_triggered(
        created, 201, cleanup, cleanup_count
    )
    body = created.json()
    asset_id = body["asset_id"]
    capability = body["capability"]

    cleanup_count = _assert_cleanup_triggered(
        await client.post(
            f"/v1/files/{asset_id}/capability",
            headers=_control_headers(control_key),
            json={"owner_subject": "alice@example.com"},
        ),
        200,
        cleanup,
        cleanup_count,
    )
    cleanup_count = _assert_cleanup_triggered(
        await client.head(f"/v1/files/{asset_id}"),
        401,
        cleanup,
        cleanup_count,
    )
    cleanup_count = _assert_cleanup_triggered(
        await client.put(
            f"/v1/files/{asset_id}/parts/1",
            headers=_data_headers(capability),
            content=b"abc",
        ),
        400,
        cleanup,
        cleanup_count,
    )
    cleanup_count = _assert_cleanup_triggered(
        await client.put(
            f"/v1/files/{asset_id}/parts/1",
            headers={
                **_data_headers(capability),
                "X-Phytomni-Part-SHA256": "0" * 64,
            },
            content=b"abc",
        ),
        422,
        cleanup,
        cleanup_count,
    )
    cleanup_count = _assert_cleanup_triggered(
        await client.post(
            f"/v1/files/{asset_id}/complete",
            headers=_data_headers(capability),
        ),
        409,
        cleanup,
        cleanup_count,
    )
    _assert_cleanup_triggered(
        await client.delete(
            f"/v1/files/{asset_id}",
            headers=_data_headers(capability),
        ),
        200,
        cleanup,
        cleanup_count,
    )


async def test_activation_metadata_is_absent_from_upload_http_surfaces(
    resumable_upload_client: tuple[
        httpx.AsyncClient,
        str,
        str,
        FakeMultipartStorage,
        list[None],
    ],
) -> None:
    """Internal activation state is absent from JSON and HEAD projections."""
    client, control_key, _ordinary_key, _storage, _cleanup = (
        resumable_upload_client
    )
    created = await _create(
        client, control_key, idempotency_key="activation-redaction"
    )
    session = created.json()
    head = await client.head(
        f"/v1/files/{session['asset_id']}",
        headers=_data_headers(session["capability"]),
    )
    content = b"abc"
    part = await client.put(
        f"/v1/files/{session['asset_id']}/parts/1",
        headers={
            **_data_headers(session["capability"]),
            "X-Phytomni-Part-SHA256": sha256(content).hexdigest(),
        },
        content=content,
    )

    assert head.status_code == 200
    assert part.status_code == 200
    assert "activated" not in created.text.lower()
    assert "activated" not in part.text.lower()
    assert not any("activated" in key.lower() for key in head.headers)
