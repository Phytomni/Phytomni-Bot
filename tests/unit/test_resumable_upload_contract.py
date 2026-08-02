# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for resumable upload metadata and stable errors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.api.resumable_uploads import (
    MAX_UPLOAD_BYTES,
    UploadContractError,
)
from mcp_server_phytomni.api.schemas import (
    AttachmentAsset,
    UploadCreateRequest,
    UploadCreateResponse,
)

pytestmark = pytest.mark.unit


def _request(**overrides: object) -> dict[str, object]:
    """Build one valid v2 create request."""
    payload: dict[str, object] = {
        "owner_subject": "owner-1",
        "filename": "sample.fastq.gz",
        "content_type": "application/octet-stream",
        "size_bytes": 1,
        "purpose": "chat_attachment",
        "idempotency_key": "create-1",
    }
    payload.update(overrides)
    return payload


def test_create_accepts_one_byte_and_ten_gib_boundaries() -> None:
    """The size ceiling is inclusive and zero bytes are not assets."""
    assert (
        UploadCreateRequest.model_validate(_request(size_bytes=1)).size_bytes
        == 1
    )
    assert (
        UploadCreateRequest.model_validate(
            _request(size_bytes=MAX_UPLOAD_BYTES)
        ).size_bytes
        == MAX_UPLOAD_BYTES
    )
    with pytest.raises(ValidationError):
        UploadCreateRequest.model_validate(
            _request(size_bytes=MAX_UPLOAD_BYTES + 1)
        )
    with pytest.raises(ValidationError):
        UploadCreateRequest.model_validate(_request(size_bytes=0))


@pytest.mark.parametrize("purpose", ["chat_attachment", "dataset", "document"])
def test_create_accepts_exact_attachment_purposes(purpose: str) -> None:
    """Accept exactly the three public upload-purpose values."""
    request = UploadCreateRequest.model_validate(_request(purpose=purpose))
    assert request.purpose == purpose


def test_create_omitted_purpose_keeps_legacy_default() -> None:
    """Keep chat attachments as the compatibility default."""
    payload = _request()
    payload.pop("purpose")
    assert UploadCreateRequest.model_validate(payload).purpose == (
        "chat_attachment"
    )


@pytest.mark.parametrize("purpose", ["", "Dataset", "agent_context", "csv"])
def test_create_rejects_non_contract_purpose(purpose: str) -> None:
    """Reject values outside the upload-purpose contract."""
    with pytest.raises(ValidationError):
        UploadCreateRequest.model_validate(_request(purpose=purpose))


@pytest.mark.parametrize("filename", ["", ".", "..", "a/b.fa", "a\\b.fa"])
def test_create_rejects_unsafe_filename(filename: str) -> None:
    """Separators, dot paths, and empty names cannot cross the boundary."""
    with pytest.raises(ValidationError):
        UploadCreateRequest.model_validate(_request(filename=filename))


def test_filename_is_nfc_normalized_and_attachment_shape_is_strict() -> None:
    """The public filename is normalized and attachment IDs are opaque."""
    request = UploadCreateRequest.model_validate(
        _request(filename="e\u0301.fastq")
    )
    assert request.filename == "\u00e9.fastq"
    assert AttachmentAsset.model_validate({"asset_id": "file_1"}).asset_id == (
        "file_1"
    )
    with pytest.raises(ValidationError):
        AttachmentAsset.model_validate(
            {"asset_id": "file_1", "obs_path": "/obs/private"}
        )


def test_stable_error_does_not_include_storage_details() -> None:
    """Public errors never echo a token, path, or SDK message."""
    error = UploadContractError("upload_capability_invalid", status_code=401)
    message = str(error)
    assert message == "upload capability is invalid"
    assert "token" not in message
    assert "/obs" not in message


def test_create_response_locks_protocol_shape() -> None:
    """Responses expose the v2 protocol and no implementation coordinates."""
    response = UploadCreateResponse(
        protocol="obs-multipart-v2",
        asset_id="file_1",
        status="uploading",
        part_size_bytes=128 * 1024**2,
        part_count=1,
        max_parallel_parts=4,
        upload_url="https://bot.example/v1/files/file_1",
        capability="opaque",
        capability_expires_at=datetime(2026, 8, 1, 0, 15, tzinfo=UTC),
        session_expires_at=datetime(2026, 8, 8, tzinfo=UTC),
    )
    dumped = response.model_dump()
    assert dumped["protocol"] == "obs-multipart-v2"
    assert "object_key" not in dumped
    assert "upload_id" not in dumped
