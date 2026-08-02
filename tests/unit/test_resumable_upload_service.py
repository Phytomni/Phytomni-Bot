# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the capability-authenticated resumable upload service."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from mcp_server_phytomni.api.resumable_uploads import (
    ResumableUploadService,
    UploadContractError,
    UploadServiceConfig,
)
from mcp_server_phytomni.api.schemas import (
    UploadCompletionRequest,
    UploadCreateRequest,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    ResumableUploadRegistry,
    UploadAssetPurpose,
)
from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    PartInput,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _request(
    *,
    owner: str = "owner-1",
    key: str = "create-1",
    size_bytes: int = 3,
    purpose: UploadAssetPurpose = "chat_attachment",
) -> UploadCreateRequest:
    """Build one trusted upload request for service tests."""
    return UploadCreateRequest(
        owner_subject=owner,
        filename="sample.fastq.gz",
        content_type="application/octet-stream",
        size_bytes=size_bytes,
        purpose=purpose,
        idempotency_key=key,
    )


def _service(
    tmp_path: Path,
    *,
    db_path: Path | None = None,
    storage: FakeMultipartStorage | None = None,
) -> tuple[ResumableUploadService, FakeMultipartStorage]:
    """Build the service with a provider-free storage boundary."""
    storage = storage or FakeMultipartStorage()
    service = ResumableUploadService(
        ResumableUploadRegistry(str(db_path or tmp_path / "uploads.db")),
        storage,
        UploadServiceConfig(
            bucket_name="bot-bucket",
            upload_origin="https://bot.example/",
            now=lambda: NOW,
        ),
    )
    return service, storage


def _part(body: bytes, digest: str | None = None) -> PartInput:
    """Build one bounded part request."""
    return PartInput(
        1,
        BytesIO(body),
        len(body),
        digest or sha256(body).hexdigest(),
    )


def _put_one(
    service: ResumableUploadService,
    response_asset_id: str,
    capability: str,
    body: bytes = b"abc",
) -> None:
    """Upload the only part of a small test asset."""
    service.put_part(response_asset_id, capability, _part(body))


def test_create_is_idempotent_and_binds_one_provider_session(
    tmp_path: Path,
) -> None:
    """Replay returns a new capability without starting another session."""
    service, storage = _service(tmp_path)
    request = _request()

    first = service.create(request)
    replay = service.create(request)

    assert first.asset_id == replay.asset_id
    assert first.capability != replay.capability
    assert first.upload_url == f"https://bot.example/v1/files/{first.asset_id}"
    assert len(storage.sessions) == 1
    assert "upload_id" not in first.model_dump_json()


def test_create_conflicts_when_only_purpose_changes(
    tmp_path: Path,
) -> None:
    """Treat a purpose-only replay change as an idempotency conflict."""
    service, storage = _service(tmp_path)
    service.create(_request())

    with pytest.raises(UploadContractError) as error:
        service.create(_request(purpose="dataset"))

    assert error.value.code == "upload_state_conflict"
    assert len(storage.sessions) == 1


@pytest.mark.parametrize("purpose", ["dataset", "document"])
def test_purpose_survives_reconstruction_and_completion(
    tmp_path: Path,
    purpose: UploadAssetPurpose,
) -> None:
    """Retain dataset and document purposes across service reconstruction."""
    db_path = tmp_path / "uploads.db"
    service, storage = _service(tmp_path, db_path=db_path)
    created = service.create(_request(purpose=purpose))
    _put_one(service, created.asset_id, created.capability)

    restarted, _restarted_storage = _service(
        tmp_path, db_path=db_path, storage=storage
    )
    renewed = restarted.renew(created.asset_id, "owner-1")
    status = restarted.head(created.asset_id, renewed.capability)
    descriptor = restarted.complete(
        created.asset_id,
        renewed.capability,
        UploadCompletionRequest(),
    )
    asset = restarted.registry.get_asset(created.asset_id, owner="owner-1")

    assert asset is not None
    assert asset.purpose == purpose
    assert status.asset_id == created.asset_id
    assert descriptor.purpose == purpose


def test_legacy_default_purpose_is_preserved_in_descriptor(
    tmp_path: Path,
) -> None:
    """Keep the legacy chat purpose in completed descriptors."""
    service, _storage = _service(tmp_path)
    created = service.create(_request())
    _put_one(service, created.asset_id, created.capability)

    descriptor = service.complete(
        created.asset_id,
        created.capability,
        UploadCompletionRequest(),
    )

    assert descriptor.purpose == "chat_attachment"


def test_part_retry_and_cross_asset_capability_are_safe(
    tmp_path: Path,
) -> None:
    """Identical retries are safe and a capability cannot cross assets."""
    service, _storage = _service(tmp_path)
    first = service.create(_request())
    second = service.create(_request(owner="owner-2", key="create-2"))
    body = b"abc"
    digest = sha256(body).hexdigest()

    accepted = service.put_part(first.asset_id, first.capability, _part(body))
    replay = service.put_part(
        first.asset_id,
        first.capability,
        _part(b"xyz", digest=digest),
    )

    assert accepted.received_parts == [1]
    assert replay.received_parts == [1]
    with pytest.raises(UploadContractError) as error:
        service.head(second.asset_id, first.capability)
    assert error.value.code == "upload_capability_invalid"


def test_part_length_and_digest_fail_with_stable_errors(
    tmp_path: Path,
) -> None:
    """The service rejects non-final length and malformed digest metadata."""
    service, _storage = _service(tmp_path)
    created = service.create(_request())

    with pytest.raises(UploadContractError) as length_error:
        service.put_part(
            created.asset_id,
            created.capability,
            _part(b"ab"),
        )
    assert length_error.value.code == "upload_state_conflict"

    with pytest.raises(UploadContractError) as digest_error:
        service.put_part(
            created.asset_id,
            created.capability,
            _part(b"abc", digest="z" * 64),
        )
    assert digest_error.value.code == "invalid_upload_metadata"


def test_complete_reconciles_unknown_provider_outcome(
    tmp_path: Path,
) -> None:
    """A provider timeout-like outcome completes only after reconciliation."""
    service, storage = _service(tmp_path)
    created = service.create(_request())
    _put_one(service, created.asset_id, created.capability)
    storage.fail_complete_unknown = True

    descriptor = service.complete(
        created.asset_id,
        created.capability,
        UploadCompletionRequest(
            sha256=sha256(b"abc").hexdigest(),
        ),
    )

    assert descriptor.asset_id == created.asset_id
    assert descriptor.filename == "sample.fastq.gz"
    assert descriptor.status == "completed"


def test_abort_releases_the_provider_session(tmp_path: Path) -> None:
    """Abort is owner-scoped and leaves no active fake provider session."""
    service, storage = _service(tmp_path)
    created = service.create(_request())

    status = service.abort(created.asset_id, created.capability)

    assert status.status == "aborted"
    state = next(iter(storage.sessions.values()))
    assert state.aborted is True
