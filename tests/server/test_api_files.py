# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``POST /v1/files`` multipart upload endpoint.

Covers the happy path (response shape + ``path`` aliasing), unauthenticated
calls (401), the pre-read Content-Length guard (413 without buffering the
body), the post-read size guard (413 after read), the empty-body rejection
(400), the filename sanitization path through the route (traversal becomes
the basename, unsafe stem characters collapse to ``-``), and the request-id
correlation between the ``X-Request-Id`` response header and the
``obs_path`` segment that embeds the request id.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api import file_upload as file_upload_module
from mcp_server_phytomni.storage import uploads as uploads_module

pytestmark = pytest.mark.server


async def test_upload_file_returns_full_response_shape(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake_obs_client: Any,
) -> None:
    """A successful upload returns FileUploadResponse with aliased path."""
    del fake_obs_client  # patches the SDK fallback as a side effect
    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("report.pdf", b"hello", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["object"] == "file"
    assert body["filename"] == "report.pdf"
    assert body["bytes"] == 5
    assert body["purpose"] == "agent_context"
    assert isinstance(body["created_at"], int) and body["created_at"] > 0
    assert body["id"].startswith("2")  # timestamped IdFactory token
    assert body["path"] == body["obs_path"]
    assert body["obs_path"].startswith("/obs/phytomni/agent_data/uploads/u1/")
    assert body["obs_path"].endswith("/report.pdf")
    assert body["id"] in body["obs_path"]


async def test_upload_file_honors_purpose_form_field(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake_obs_client: Any,
) -> None:
    """A caller-supplied purpose round-trips into the response."""
    del fake_obs_client
    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("x.bin", b"data", "application/octet-stream")},
        data={"purpose": "supplementary"},
    )

    assert response.status_code == 201
    assert response.json()["purpose"] == "supplementary"


async def test_upload_file_rejects_unauthenticated_request(
    api_client: httpx.AsyncClient,
) -> None:
    """Missing bearer token returns 401."""
    response = await api_client.post(
        "/v1/files",
        files={"file": ("x.bin", b"data", "application/octet-stream")},
    )

    assert response.status_code == 401


async def test_upload_file_pre_read_rejects_oversized_content_length(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client: Any,
) -> None:
    """A Content-Length above the ceiling returns 413 without writing."""
    real_config = api_app.ApiConfig
    monkeypatch.setattr(
        file_upload_module,
        "ApiConfig",
        lambda: SimpleNamespace(
            API_RATE_LIMIT_PER_MIN=120,
            API_UPLOAD_MAX_BYTES=8,
            API_UPLOAD_PREFIX="agent_data/uploads",
            **{
                k: getattr(real_config(), k)
                for k in (
                    "API_KEYS_DB_PATH",
                    "API_TASKS_DB_PATH",
                    "API_REQUEST_TIMEOUT",
                    "API_RUN_TTL_OK_HOURS",
                    "API_RUN_TTL_FAIL_DAYS",
                )
            },
        ),
    )

    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={
            "file": (
                "x.bin",
                b"this is more than 8 bytes",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == 413
    assert "put_content" not in fake_obs_client.captured


async def test_upload_file_post_read_rejects_oversize_when_header_absent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized body still returns 413 if the helper's check fires."""
    monkeypatch.setattr(
        uploads_module,
        "_SERVER_DEFAULTS",
        SimpleNamespace(BUCKET_NAME="phytomni", OBS_SERVER="x"),
    )

    def _boom(**_: Any) -> Any:
        raise uploads_module.UploadTooLargeError(
            "upload of 999 bytes exceeds max_bytes=8"
        )

    monkeypatch.setattr(uploads_module, "upload_user_file", _boom)
    monkeypatch.setattr(file_upload_module, "upload_user_file", _boom)

    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("x.bin", b"tiny", "application/octet-stream")},
    )

    assert response.status_code == 413


async def test_upload_file_byte_budget_breach_returns_413_without_writing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_obs_client: Any,
) -> None:
    """A chunked-transfer body that breaches max_bytes returns 413 and never calls OBS.

    Covers AF-001 (audit 2026-05-26): when the Content-Length header is
    absent or falsified, the route must still bound peak memory and
    never reach ``upload_user_file``. We monkeypatch
    ``read_with_byte_budget`` to return ``None`` (the helper's "budget
    breached" signal) so the route's None-branch fires regardless of
    what the in-process ASGI client claims for Content-Length.
    """

    async def _budget_breach(*_args: Any, **_kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(
        file_upload_module, "read_with_byte_budget", _budget_breach
    )

    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("x.bin", b"tiny", "application/octet-stream")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == 413
    assert "put_content" not in fake_obs_client.captured


async def test_upload_file_rejects_empty_body(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake_obs_client: Any,
) -> None:
    """An empty multipart body returns 400."""
    del fake_obs_client
    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("x.bin", b"", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == 400


async def test_upload_file_sanitizes_traversal_filename(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake_obs_client: Any,
) -> None:
    """A path-traversing filename collapses to the basename."""
    del fake_obs_client
    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={
            "file": (
                "../../etc/passwd",
                b"data",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "passwd"
    assert "../" not in body["obs_path"]
    assert body["obs_path"].endswith("/passwd")


async def test_upload_file_request_id_appears_in_obs_path(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    fake_obs_client: Any,
) -> None:
    """The X-Request-Id header value is the request-id segment of obs_path."""
    del fake_obs_client
    response = await api_client.post(
        "/v1/files",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        files={"file": ("notes.txt", b"abc", "text/plain")},
    )

    assert response.status_code == 201
    request_id = response.headers["X-Request-Id"]
    body = response.json()
    assert f"/agent_data/uploads/u1/{request_id}/" in body["obs_path"]
