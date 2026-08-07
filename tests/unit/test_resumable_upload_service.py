# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the capability-authenticated resumable upload service."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    AssetCreateSpec,
    CapabilityAuthorization,
    PartRecord,
    ResumableUploadRegistry,
    ResumableUploadRegistryConfig,
    UploadAssetPurpose,
)
from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    MultipartSession,
    MultipartStorageError,
    PartInput,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 8, 1, tzinfo=UTC)


class _FailingMultipartStorage(FakeMultipartStorage):
    """Record safe call counts while injecting normalized provider failures."""

    def __init__(
        self,
        *,
        fail_begin_calls: int = 0,
        fail_abort_calls: int = 0,
        begin_error_code: str = "upload_storage_unavailable",
    ) -> None:
        super().__init__()
        self.begin_calls = 0
        self.abort_calls = 0
        self.fail_begin_calls = fail_begin_calls
        self.fail_abort_calls = fail_abort_calls
        self.begin_error_code = begin_error_code
        self.after_begin: Callable[[MultipartSession], None] | None = None

    def begin(self, *, bucket: str, object_key: str) -> MultipartSession:
        """Start a fake session or raise one stable storage error."""
        self.begin_calls += 1
        if self.fail_begin_calls:
            self.fail_begin_calls -= 1
            raise MultipartStorageError(self.begin_error_code)
        session = super().begin(bucket=bucket, object_key=object_key)
        if self.after_begin is not None:
            self.after_begin(session)
        return session

    def abort(self, session: MultipartSession) -> None:
        """Abort a fake session or raise one stable storage error."""
        self.abort_calls += 1
        if self.fail_abort_calls:
            self.fail_abort_calls -= 1
            raise MultipartStorageError("upload_storage_unavailable")
        super().abort(session)


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


def test_begin_failure_discards_only_unbound_allocation(
    tmp_path: Path,
) -> None:
    """A failed provider begin frees local capacity but keeps create volume."""
    storage = _FailingMultipartStorage(fail_begin_calls=1)
    service, _storage = _service(tmp_path, storage=storage)

    with pytest.raises(UploadContractError) as captured:
        service.create(_request())

    assert captured.value.code == "upload_storage_unavailable"
    assert str(captured.value) == "upload storage unavailable"
    recreated = service.create(_request())
    service.create(_request(key="create-2"))
    service.create(_request(key="create-3"))

    assert recreated.status == "uploading"
    assert storage.begin_calls == 4
    with sqlite3.connect(service.registry.db_path) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM upload_assets WHERE status = 'uploading'"
        ).fetchone()[0]
        accepted_bytes, event_count = conn.execute(
            "SELECT SUM(byte_size), COUNT(event_kind) "
            "FROM upload_quota_events "
            "WHERE owner_subject = ? AND event_kind = 'create'",
            ("owner-1",),
        ).fetchone()
    assert active_count == 3
    assert (event_count, accepted_bytes) == (4, 12)


def test_losing_bind_aborts_only_its_provider_session(
    tmp_path: Path,
) -> None:
    """A concurrent winning bind survives compensation by the loser."""
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.db"))
    allocated, _secret = registry.create_or_replay(
        AssetCreateSpec(
            owner_subject="owner-1",
            filename="sample.fastq.gz",
            content_type="application/octet-stream",
            size_bytes=3,
            purpose="chat_attachment",
            idempotency_key="create-1",
        ),
        now=NOW,
    )
    storage = _FailingMultipartStorage()

    def bind_winner(_losing_session: MultipartSession) -> None:
        registry.set_provider_session(
            allocated.asset_id,
            owner=allocated.owner_subject,
            obs_upload_id="winner-provider-session",
            now=NOW,
        )

    storage.after_begin = bind_winner
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="bot-bucket",
            upload_origin="https://bot.example/",
            now=lambda: NOW,
        ),
    )

    with pytest.raises(UploadContractError) as captured:
        service.create(_request())

    assert captured.value.code == "upload_state_conflict"
    assert str(captured.value) == "upload state conflict"
    assert storage.abort_calls == 1
    survivor = registry.get_asset(allocated.asset_id, owner="owner-1")
    assert survivor is not None
    assert survivor.status == "uploading"
    assert survivor.obs_upload_id is not None
    assert (
        registry.discard_unbound_allocation(
            allocated.asset_id,
            owner="owner-1",
        )
        is False
    )


def test_discard_rejects_activated_and_part_bearing_allocations(
    tmp_path: Path,
) -> None:
    """Conditional discard leaves non-pristine allocations untouched."""
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.db"))
    activated, activated_secret = registry.create_or_replay(
        AssetCreateSpec(
            "owner-1",
            "a.bin",
            "application/octet-stream",
            3,
            "chat_attachment",
            "activated",
        ),
        now=NOW,
    )
    registry.authorize_capability(
        activated_secret.raw_token,
        asset_id=activated.asset_id,
        authorization=CapabilityAuthorization("head", True),
        now=NOW,
    )
    part_bearing, _secret = registry.create_or_replay(
        AssetCreateSpec(
            "owner-2",
            "b.bin",
            "application/octet-stream",
            3,
            "chat_attachment",
            "part-bearing",
        ),
        now=NOW,
    )
    registry.record_part(
        PartRecord(part_bearing.asset_id, 1, 3, "a" * 64, "opaque", NOW),
        now=NOW,
    )

    assert not registry.discard_unbound_allocation(
        activated.asset_id, owner="owner-1"
    )
    assert not registry.discard_unbound_allocation(
        part_bearing.asset_id, owner="owner-2"
    )


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


def test_abort_retains_the_provider_session_for_cleanup(
    tmp_path: Path,
) -> None:
    """Abort is owner-scoped and defers provider work to cleanup."""
    storage = _FailingMultipartStorage()
    service, _storage = _service(tmp_path, storage=storage)
    created = service.create(_request())

    status = service.abort(created.asset_id, created.capability)

    assert status.status == "aborted"
    assert storage.abort_calls == 0


def test_abort_releases_local_quota_before_provider_cleanup(
    tmp_path: Path,
) -> None:
    """DELETE terminalizes locally and admits another create immediately."""
    storage = _FailingMultipartStorage(fail_abort_calls=1)
    service, _storage = _service(tmp_path, storage=storage)
    created = [
        service.create(_request(key=f"create-{index}")) for index in range(3)
    ]

    status = service.abort(created[0].asset_id, created[0].capability)
    admitted = service.create(_request(key="create-4"))

    assert status.status == "aborted"
    assert admitted.status == "uploading"
    assert storage.abort_calls == 0
    durable = service.registry.get_asset(created[0].asset_id, owner="owner-1")
    assert durable is not None
    assert durable.status == "aborted"
    assert durable.reserved_bytes == 0
    assert durable.obs_upload_id is not None


def test_repeated_abort_accepts_only_the_same_live_aborted_capability(
    tmp_path: Path,
) -> None:
    """Only an unexpired capability for its already-aborted row may replay."""
    clock = [NOW]
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.db"))
    service = ResumableUploadService(
        registry,
        FakeMultipartStorage(),
        UploadServiceConfig(
            bucket_name="bot-bucket",
            upload_origin="https://bot.example/",
            now=lambda: clock[0],
        ),
    )
    aborted = service.create(_request())
    other = service.create(_request(owner="owner-2", key="other"))

    first = service.abort(aborted.asset_id, aborted.capability)
    replay = service.abort(aborted.asset_id, aborted.capability)

    assert first.status == "aborted"
    assert replay.status == "aborted"
    with pytest.raises(UploadContractError) as cross_asset:
        service.abort(other.asset_id, aborted.capability)
    assert cross_asset.value.code == "upload_capability_invalid"
    with pytest.raises(UploadContractError) as invalid_token:
        service.abort(aborted.asset_id, "not-a-capability")
    assert invalid_token.value.code == "upload_capability_invalid"

    clock[0] = NOW + timedelta(minutes=15)
    with pytest.raises(UploadContractError) as expired_token:
        service.abort(aborted.asset_id, aborted.capability)
    assert expired_token.value.code == "upload_capability_invalid"


def test_repeated_abort_rejects_a_distinct_pre_abort_capability(
    tmp_path: Path,
) -> None:
    """Only the token that performed DELETE may replay it."""
    service, _storage = _service(tmp_path)
    first = service.create(_request())
    second = service.create(_request())

    service.abort(first.asset_id, first.capability)

    with pytest.raises(UploadContractError) as distinct:
        service.abort(first.asset_id, second.capability)

    assert distinct.value.code == "upload_capability_invalid"
    assert service.abort(first.asset_id, first.capability).status == "aborted"


def test_bind_database_failure_compensates_and_preserves_create_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed provider binding aborts the new session and frees its row."""
    storage = _FailingMultipartStorage()
    service, _storage = _service(tmp_path, storage=storage)
    original_bind = service.registry.set_provider_session
    bind_calls = 0

    def fail_bind(
        asset_id: str,
        *,
        owner: str,
        obs_upload_id: str,
        now: datetime,
    ) -> object:
        nonlocal bind_calls
        bind_calls += 1
        if bind_calls == 1:
            raise sqlite3.OperationalError("database is unavailable")
        return original_bind(
            asset_id,
            owner=owner,
            obs_upload_id=obs_upload_id,
            now=now,
        )

    monkeypatch.setattr(service.registry, "set_provider_session", fail_bind)

    with pytest.raises(UploadContractError) as captured:
        service.create(_request())

    assert captured.value.code == "upload_storage_unavailable"
    assert captured.value.status_code == 503
    assert str(captured.value) == "upload storage unavailable"
    assert storage.abort_calls == 1
    recreated = service.create(_request())
    assert recreated.status == "uploading"

    with sqlite3.connect(service.registry.db_path) as conn:
        active_count, accepted_bytes, event_count = conn.execute(
            "SELECT (SELECT COUNT(*) FROM upload_assets "
            "WHERE status = 'uploading'), "
            "(SELECT COALESCE(SUM(byte_size), 0) FROM upload_quota_events "
            "WHERE owner_subject = 'owner-1' AND event_kind = 'create'), "
            "(SELECT COUNT(*) FROM upload_quota_events "
            "WHERE owner_subject = 'owner-1' AND event_kind = 'create')"
        ).fetchone()

    assert (active_count, accepted_bytes, event_count) == (1, 6, 2)


def test_unknown_provider_code_maps_to_generic_storage_contract(
    tmp_path: Path,
) -> None:
    """Provider implementation codes never cross the public boundary."""
    storage = _FailingMultipartStorage(
        fail_begin_calls=1,
        begin_error_code="provider-secret-internal-code",
    )
    service, _storage = _service(tmp_path, storage=storage)

    with pytest.raises(UploadContractError) as captured:
        service.create(_request())

    assert captured.value.code == "upload_storage_unavailable"
    assert captured.value.status_code == 503
    assert str(captured.value) == "upload storage unavailable"
    assert "provider-secret-internal-code" not in str(captured.value)


def test_repeated_abort_rejects_completed_and_expired_rows(
    tmp_path: Path,
) -> None:
    """Terminal rows other than aborted never accept DELETE replay."""
    clock = [NOW]
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.db"))
    service = ResumableUploadService(
        registry,
        FakeMultipartStorage(),
        UploadServiceConfig(
            bucket_name="bot-bucket",
            upload_origin="https://bot.example/",
            now=lambda: clock[0],
        ),
    )
    completed = service.create(_request(key="completed"))
    _put_one(service, completed.asset_id, completed.capability)
    service.complete(
        completed.asset_id,
        completed.capability,
        UploadCompletionRequest(),
    )
    expired = service.create(_request(key="expired"))
    clock[0] = NOW + timedelta(minutes=180)
    service.cleanup_expired()
    expired_record = registry.get_asset(expired.asset_id, owner="owner-1")
    assert expired_record is not None
    assert expired_record.status == "expired"

    for asset_id, capability in (
        (completed.asset_id, completed.capability),
        (expired.asset_id, expired.capability),
    ):
        with pytest.raises(UploadContractError) as captured:
            service.abort(asset_id, capability)
        assert captured.value.code == "upload_capability_invalid"


def test_cleanup_retries_failed_provider_abort_without_reopening_local_state(
    tmp_path: Path,
) -> None:
    """A normalized provider failure retains durable cleanup work for retry."""
    storage = _FailingMultipartStorage(fail_abort_calls=1)
    service, _storage = _service(tmp_path, storage=storage)
    created = service.create(_request())
    service.abort(created.asset_id, created.capability)

    first_pass = service.cleanup_expired()
    after_failure = service.registry.get_asset(
        created.asset_id, owner="owner-1"
    )
    second_pass = service.cleanup_expired()
    after_success = service.registry.get_asset(
        created.asset_id, owner="owner-1"
    )

    assert first_pass == (created.asset_id,)
    assert second_pass == (created.asset_id,)
    assert storage.abort_calls == 2
    assert after_failure is not None
    assert after_failure.status == "aborted"
    assert after_failure.reserved_bytes == 0
    assert after_failure.obs_upload_id is not None
    assert after_success is not None
    assert after_success.status == "aborted"
    assert after_success.obs_upload_id is None


def test_cleanup_isolates_provider_abort_failures_between_rows(
    tmp_path: Path,
) -> None:
    """One provider failure does not block a later terminal row."""
    storage = _FailingMultipartStorage(fail_abort_calls=1)
    service, _storage = _service(tmp_path, storage=storage)
    created = [
        service.create(_request(key=f"cleanup-{index}")) for index in range(2)
    ]
    for item in created:
        service.abort(item.asset_id, item.capability)

    pending = service.cleanup_expired()
    durable = [
        service.registry.get_asset(item.asset_id, owner="owner-1")
        for item in created
    ]

    assert set(pending) == {item.asset_id for item in created}
    assert storage.abort_calls == 2
    assert all(item is not None for item in durable)
    assert [
        item.obs_upload_id is None for item in durable if item is not None
    ].count(True) == 1


def test_service_activation_and_stale_renewal_use_registry_policy(
    tmp_path: Path,
) -> None:
    """Service operations pass takeover intent and preserve expiry errors."""
    clock = [NOW]
    registry = ResumableUploadRegistry(
        str(tmp_path / "uploads.db"),
        ResumableUploadRegistryConfig(provisional_ttl=timedelta(minutes=1)),
    )
    service = ResumableUploadService(
        registry,
        FakeMultipartStorage(),
        UploadServiceConfig(
            bucket_name="bot-bucket",
            upload_origin="https://bot.example/",
            now=lambda: clock[0],
        ),
    )
    aborted = service.create(_request(key="abort"))
    service.abort(aborted.asset_id, aborted.capability)
    aborted_record = registry.get_asset(aborted.asset_id, owner="owner-1")
    assert aborted_record is not None
    assert aborted_record.activated_at is None

    headed = service.create(_request(key="head"))
    clock[0] = NOW + timedelta(seconds=1)
    service.head(headed.asset_id, headed.capability)
    headed_record = registry.get_asset(headed.asset_id, owner="owner-1")
    assert headed_record is not None
    assert headed_record.activated_at == clock[0]

    stale = service.create(_request(key="stale"))
    clock[0] = NOW + timedelta(minutes=2)
    with pytest.raises(UploadContractError) as error:
        service.renew(stale.asset_id, "owner-1")
    assert error.value.code == "upload_session_expired"
    assert error.value.status_code == 410
