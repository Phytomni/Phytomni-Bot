# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the public resumable-upload response schema."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.api.schemas import (
    UploadCreateRequest,
    UploadCreateResponse,
)

pytestmark = pytest.mark.server


def _request_payload() -> dict[str, object]:
    """Build one valid upload-create request payload."""
    return {
        "owner_subject": "owner@example.com",
        "filename": "sample.fastq.gz",
        "content_type": "application/gzip",
        "size_bytes": 3,
        "purpose": "document",
        "idempotency_key": "schema-test-1",
    }


def test_create_request_accepts_canonical_content_type() -> None:
    """The Bot create-upload request serializes its canonical field."""
    request = UploadCreateRequest.model_validate(_request_payload())

    assert request.content_type == "application/gzip"
    assert request.model_dump()["content_type"] == "application/gzip"
    assert "content_type_hint" not in request.model_dump()


def test_create_request_rejects_obsolete_content_type_hint() -> None:
    """The obsolete Bot-boundary field is rejected as an extra field."""
    payload = _request_payload()
    del payload["content_type"]
    payload["content_type_hint"] = "application/gzip"

    with pytest.raises(ValidationError, match="content_type_hint"):
        UploadCreateRequest.model_validate(payload)


def _make() -> UploadCreateResponse:
    """Build a representative safe v2 create response."""
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    return UploadCreateResponse(
        protocol="obs-multipart-v2",
        asset_id="file_asset",
        status="uploading",
        part_size_bytes=128 * 1024**2,
        part_count=2,
        max_parallel_parts=4,
        upload_url="http://127.0.0.1:8080/v1/files/file_asset",
        capability="opaque-capability",
        capability_expires_at=timestamp,
        session_expires_at=timestamp,
    )


def test_create_response_contains_only_safe_v2_fields() -> None:
    """The create response exposes the browser contract, not OBS details."""
    dumped = _make().model_dump()

    assert set(dumped) == {
        "protocol",
        "asset_id",
        "status",
        "part_size_bytes",
        "part_count",
        "max_parallel_parts",
        "upload_url",
        "capability",
        "capability_expires_at",
        "session_expires_at",
    }


def test_create_response_rejects_provider_fields() -> None:
    """Provider object keys and upload ids cannot enter the public model."""
    payload = _make().model_dump()
    payload["bucket"] = "phytomni"

    with pytest.raises(ValidationError):
        UploadCreateResponse.model_validate(payload)
