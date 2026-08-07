# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared v2 upload constants and sanitized contract errors."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Literal, cast

from ..runtime.resumable_uploads import (
    AssetCreateSpec,
    AssetRecord,
    CapabilityAuthorization,
    CapabilityRecord,
    PartRecord,
    ResumableUploadRegistry,
    UploadStateError,
)
from ..storage.multipart import (
    MultipartSession,
    MultipartStorage,
    MultipartStorageError,
    PartInput,
    StoredPart,
)
from .asset_descriptors import build_asset_descriptor
from .schemas import (
    AssetDescriptor,
    UploadCapabilityResponse,
    UploadCompletionRequest,
    UploadCreateRequest,
    UploadCreateResponse,
    UploadPartResponse,
    UploadStatusResponse,
)

__all__ = [
    "MAX_UPLOAD_BYTES",
    "MAX_PARALLEL_PARTS",
    "PART_SIZE_BYTES",
    "ResumableUploadService",
    "UploadServiceConfig",
    "UPLOAD_PROTOCOL",
    "UploadContractError",
]

UPLOAD_PROTOCOL: Literal["obs-multipart-v2"] = "obs-multipart-v2"
PART_SIZE_BYTES = 128 * 1024**2
MAX_UPLOAD_BYTES = 10 * 1024**3
MAX_PARALLEL_PARTS = 4
_ACTIVATION_OPERATIONS = frozenset({"head", "part", "complete"})


class UploadContractError(ValueError):
    """A stable upload error that cannot expose storage implementation data."""

    __slots__ = ("code", "status_code", "retryable")

    def __init__(
        self, code: str, status_code: int, retryable: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable

    def __str__(self) -> str:
        """Return a fixed public message keyed only by the stable code."""
        messages = {
            "invalid_upload_metadata": "upload metadata is invalid",
            "upload_capability_invalid": "upload capability is invalid",
            "upload_asset_not_found": "upload asset was not found",
            "upload_state_conflict": "upload state conflict",
            "upload_session_expired": "upload session expired",
            "upload_limit_exceeded": "upload limit exceeded",
            "upload_checksum_mismatch": "upload checksum mismatch",
            "upload_rate_limited": "upload rate limited",
            "attachment_purpose_invalid": "attachment purpose is invalid",
            "obs_outcome_unknown": "upload storage outcome is unknown",
            "upload_storage_unavailable": "upload storage unavailable",
            "unsupported_asset_format": "asset format is unsupported",
        }
        return messages.get(self.code, "upload request failed")


@dataclass(frozen=True, slots=True)
class UploadServiceConfig:
    """Runtime coordinates for one resumable-upload service instance."""

    bucket_name: str
    upload_origin: str
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    part_size_bytes: int = PART_SIZE_BYTES
    max_parallel_parts: int = MAX_PARALLEL_PARTS
    now: Callable[[], datetime] | None = None


class ResumableUploadService:
    """Apply the v2 contract at the Bot-owned storage boundary."""

    def __init__(
        self,
        registry: ResumableUploadRegistry,
        storage: MultipartStorage,
        config: UploadServiceConfig,
    ) -> None:
        self.registry = registry
        self.storage = storage
        self.config = config
        self.bucket_name = config.bucket_name
        self.upload_origin = config.upload_origin.rstrip("/")
        self._now = config.now or _utc_now

    @property
    def max_upload_bytes(self) -> int:
        """Return the configured maximum asset size."""
        return self.config.max_upload_bytes

    @property
    def part_size_bytes(self) -> int:
        """Return the configured maximum request part size."""
        return self.config.part_size_bytes

    def create(self, request: UploadCreateRequest) -> UploadCreateResponse:
        """Create or replay an upload and start its provider session."""
        if request.size_bytes > self.max_upload_bytes:
            raise UploadContractError("upload_limit_exceeded", status_code=413)
        spec = AssetCreateSpec(
            owner_subject=request.owner_subject,
            filename=request.filename,
            content_type=request.content_type,
            size_bytes=request.size_bytes,
            purpose=request.purpose,
            idempotency_key=request.idempotency_key,
        )
        now = self._now()
        try:
            asset, capability = self.registry.create_or_replay(spec, now=now)
            if asset.obs_upload_id is None:
                try:
                    session = self.storage.begin(
                        bucket=self.bucket_name,
                        object_key=asset.object_key,
                    )
                except MultipartStorageError:
                    _discard_quietly(self.registry, asset)
                    raise
                try:
                    asset = self.registry.set_provider_session(
                        asset.asset_id,
                        owner=asset.owner_subject,
                        obs_upload_id=session.upload_id,
                        now=now,
                    )
                except UploadStateError:
                    _abort_quietly(self.storage, session)
                    _discard_quietly(self.registry, asset)
                    raise
        except UploadStateError as error:
            raise _contract_error(error) from error
        except MultipartStorageError as error:
            raise _contract_error(error) from error
        return UploadCreateResponse(
            protocol=UPLOAD_PROTOCOL,
            asset_id=asset.asset_id,
            status="uploading",
            part_size_bytes=asset.part_size_bytes,
            part_count=asset.part_count,
            max_parallel_parts=self.config.max_parallel_parts,
            upload_url=f"{self.upload_origin}/v1/files/{asset.asset_id}",
            capability=capability.raw_token,
            capability_expires_at=capability.record.expires_at,
            session_expires_at=asset.session_expires_at,
        )

    def renew(
        self, asset_id: str, owner_subject: str
    ) -> UploadCapabilityResponse:
        """Issue a fresh capability for a trusted owner delegation."""
        try:
            capability = self.registry.issue_capability(
                asset_id,
                owner=owner_subject,
                now=self._now(),
                operations=("head", "part", "complete", "abort"),
            )
            asset = self.registry.get_asset(asset_id, owner=owner_subject)
            if asset is None:
                raise UploadStateError("upload_asset_not_found")
        except UploadStateError as error:
            raise _contract_error(error) from error
        return UploadCapabilityResponse(
            protocol=UPLOAD_PROTOCOL,
            asset_id=asset_id,
            status="uploading",
            upload_url=f"{self.upload_origin}/v1/files/{asset_id}",
            capability=capability.raw_token,
            capability_expires_at=capability.record.expires_at,
            session_expires_at=asset.session_expires_at,
        )

    def head(self, asset_id: str, capability: str) -> UploadStatusResponse:
        """Return owner-scoped received-part metadata."""
        try:
            _record, asset = self._authorized_asset(
                asset_id, capability, operation="head"
            )
            return self._status(asset)
        except UploadStateError as error:
            raise _contract_error(error) from error

    def authorize(
        self, asset_id: str, capability: str, *, operation: str
    ) -> None:
        """Validate a capability before a route starts consuming a body."""
        try:
            self._authorized_asset(asset_id, capability, operation=operation)
        except UploadStateError as error:
            raise _contract_error(error) from error

    def put_part(
        self,
        asset_id: str,
        capability: str,
        upload: PartInput,
    ) -> UploadPartResponse:
        """Stream and persist one exact-length authoritative part."""
        lease_id: str | None = None
        try:
            record, asset = self._authorized_asset(
                asset_id, capability, operation="part"
            )
            _validate_part_request(
                asset,
                upload.part_number,
                upload.content_length,
                upload.sha256,
            )
            existing = self.registry.get_part(
                asset_id, upload.part_number, owner=record.owner_subject
            )
            if existing is not None:
                if _same_part(existing, upload.content_length, upload.sha256):
                    return self._part_status(asset, existing)
                raise UploadStateError("upload_state_conflict")
            lease_id = self.registry.acquire_part_lease(
                asset_id,
                owner=record.owner_subject,
                now=self._now(),
            )
            with _stage_part(
                upload.source,
                upload.content_length,
                upload.sha256,
                max_spool_bytes=self.part_size_bytes,
            ) as staged:
                stored = self.storage.put_part(
                    _session_for(asset, self.bucket_name),
                    PartInput(
                        upload.part_number,
                        staged,
                        upload.content_length,
                        upload.sha256.lower(),
                    ),
                )
            _validate_stored_part(
                stored,
                upload.part_number,
                upload.content_length,
                upload.sha256,
            )
            part = self.registry.record_part(
                PartRecord(
                    asset_id=asset_id,
                    part_number=stored.part_number,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256.lower(),
                    etag=stored.etag,
                    received_at=self._now(),
                ),
                now=self._now(),
            )
            return self._part_status(asset, part)
        except UploadStateError as error:
            raise _contract_error(error) from error
        except MultipartStorageError as error:
            raise _contract_error(error) from error
        finally:
            if lease_id is not None:
                self.registry.release_part_lease(lease_id)

    def complete(
        self,
        asset_id: str,
        capability: str,
        request: UploadCompletionRequest,
    ) -> AssetDescriptor:
        """Complete an asset after an ambiguous provider result."""
        try:
            _record, asset = self._authorized_asset(
                asset_id, capability, operation="complete"
            )
            if request.sha256 is not None:
                _validate_checksum(request.sha256)
            parts = self.registry.get_parts(
                asset_id, owner=asset.owner_subject
            )
            _validate_complete_parts(asset, parts)
            stored_parts = tuple(
                StoredPart(
                    part.part_number,
                    part.etag,
                    part.byte_size,
                    part.sha256,
                )
                for part in parts
            )
            session = _session_for(asset, self.bucket_name)
            try:
                completed = self.storage.complete(session, stored_parts)
            except MultipartStorageError as error:
                if error.code != "obs_outcome_unknown":
                    raise
                reconciled = self.storage.reconcile_complete(
                    session, stored_parts
                )
                if reconciled is None:
                    raise MultipartStorageError(
                        "obs_outcome_unknown"
                    ) from error
                completed = reconciled
            if completed.byte_size != asset.size_bytes:
                raise MultipartStorageError("upload_state_conflict")
            completed_asset = self.registry.complete_asset(
                asset_id,
                owner=asset.owner_subject,
                now=self._now(),
            )
        except UploadStateError as error:
            raise _contract_error(error) from error
        except MultipartStorageError as error:
            raise _contract_error(error) from error
        return _descriptor(completed_asset)

    def abort(self, asset_id: str, capability: str) -> UploadStatusResponse:
        """Abort locally while retaining provider cleanup work for retry."""
        try:
            _record, asset = self._authorized_asset(
                asset_id, capability, operation="abort"
            )
            aborted = self.registry.abort_asset(
                asset_id,
                owner=asset.owner_subject,
                now=self._now(),
            )
            return self._status(aborted)
        except UploadStateError as error:
            raise _contract_error(error) from error

    def cleanup_expired(self) -> tuple[str, ...]:
        """Expire stale sessions and retry every terminal provider cleanup."""
        pending = self.registry.cleanup_expired(now=self._now())
        for asset_id in pending:
            asset = self.registry.get_asset_by_id(asset_id)
            if (
                asset is not None
                and asset.obs_upload_id is not None
                and _abort_quietly(
                    self.storage, _session_for(asset, self.bucket_name)
                )
            ):
                self.registry.clear_provider_session(asset_id, now=self._now())
        return pending

    def _authorized_asset(
        self, asset_id: str, capability: str, *, operation: str
    ) -> tuple[CapabilityRecord, AssetRecord]:
        """Authorize an operation and record data-plane takeover only."""
        return self.registry.authorize_capability(
            capability,
            asset_id=asset_id,
            authorization=CapabilityAuthorization(
                operation,
                operation in _ACTIVATION_OPERATIONS,
            ),
            now=self._now(),
        )

    def _status(self, asset: AssetRecord) -> UploadStatusResponse:
        """Build a safe status response without provider coordinates."""
        parts = self.registry.get_parts(
            asset.asset_id, owner=asset.owner_subject
        )
        return UploadStatusResponse(
            protocol=UPLOAD_PROTOCOL,
            asset_id=asset.asset_id,
            status=asset.status,
            size_bytes=asset.size_bytes,
            part_size_bytes=asset.part_size_bytes,
            part_count=asset.part_count,
            received_parts=[part.part_number for part in parts],
            filename=asset.filename,
        )

    def _part_status(
        self, asset: AssetRecord, part: PartRecord
    ) -> UploadPartResponse:
        """Build a safe response for a new or idempotent part."""
        parts = self.registry.get_parts(
            asset.asset_id, owner=asset.owner_subject
        )
        return UploadPartResponse(
            protocol=UPLOAD_PROTOCOL,
            asset_id=asset.asset_id,
            status="uploading",
            part_number=part.part_number,
            byte_size=part.byte_size,
            received_parts=[item.part_number for item in parts],
        )


def _contract_error(
    error: UploadStateError | MultipartStorageError,
) -> UploadContractError:
    """Map internal state and storage codes to stable public errors."""
    status_codes = {
        "invalid_upload_metadata": 400,
        "upload_capability_invalid": 401,
        "upload_asset_not_found": 404,
        "upload_state_conflict": 409,
        "upload_session_expired": 410,
        "upload_limit_exceeded": 413,
        "attachment_purpose_invalid": 422,
        "upload_checksum_mismatch": 422,
        "upload_rate_limited": 429,
        "unsupported_asset_format": 415,
        "obs_outcome_unknown": 503,
        "upload_storage_unavailable": 503,
    }
    code = error.code
    status_code = status_codes.get(code, 500)
    return UploadContractError(
        code=code,
        status_code=status_code,
        retryable=code
        in {
            "upload_rate_limited",
            "obs_outcome_unknown",
            "upload_storage_unavailable",
        },
    )


def _validate_part_request(
    asset: AssetRecord,
    part_number: int,
    content_length: int,
    expected_sha256: str,
) -> None:
    """Validate the exact length and digest declared for one part."""
    if not 1 <= part_number <= asset.part_count:
        raise UploadStateError("upload_state_conflict")
    if content_length < 0:
        raise UploadStateError("invalid_upload_metadata")
    _validate_checksum(expected_sha256)
    if part_number < asset.part_count:
        expected_length = asset.part_size_bytes
    else:
        expected_length = (
            asset.size_bytes - (asset.part_count - 1) * asset.part_size_bytes
        )
    if content_length != expected_length:
        raise UploadStateError("upload_state_conflict")


def _validate_checksum(value: str) -> None:
    """Require a lowercase-insensitive SHA-256 hexadecimal digest."""
    if len(value) != 64:
        raise UploadStateError("invalid_upload_metadata")
    try:
        int(value, 16)
    except ValueError as error:
        raise UploadStateError("invalid_upload_metadata") from error


@contextmanager
def _stage_part(
    source: BinaryIO,
    content_length: int,
    expected_sha256: str,
    *,
    max_spool_bytes: int = PART_SIZE_BYTES,
) -> Iterator[BinaryIO]:
    """Validate one bounded part without buffering the complete asset."""
    with SpooledTemporaryFile(max_size=max_spool_bytes, mode="w+b") as staged:
        digest = sha256()
        total = 0
        try:
            while total < content_length:
                block = source.read(min(1024 * 1024, content_length - total))
                if not block:
                    raise MultipartStorageError("upload_state_conflict")
                if len(block) > content_length - total:
                    raise MultipartStorageError("upload_state_conflict")
                staged.write(block)
                digest.update(block)
                total += len(block)
            if source.read(1):
                raise MultipartStorageError("upload_state_conflict")
            if digest.hexdigest() != expected_sha256.lower():
                raise MultipartStorageError("upload_checksum_mismatch")
            staged.seek(0)
            yield cast(BinaryIO, staged)
        except MultipartStorageError:
            raise
        except (OSError, ValueError) as error:
            raise MultipartStorageError(
                "upload_storage_unavailable"
            ) from error


def _same_part(
    part: PartRecord, content_length: int, expected_sha256: str
) -> bool:
    """Compare retry metadata without exposing the internal ETag."""
    return (
        part.byte_size == content_length
        and part.sha256.lower() == expected_sha256.lower()
    )


def _validate_stored_part(
    stored: StoredPart,
    part_number: int,
    content_length: int,
    expected_sha256: str,
) -> None:
    """Reject a storage adapter result that violates the request contract."""
    if stored.part_number != part_number or stored.byte_size != content_length:
        raise MultipartStorageError("upload_state_conflict")
    if stored.sha256.lower() != expected_sha256.lower():
        raise MultipartStorageError("upload_checksum_mismatch")
    if not stored.etag:
        raise MultipartStorageError("upload_storage_unavailable")


def _validate_complete_parts(
    asset: AssetRecord, parts: Sequence[PartRecord]
) -> None:
    """Require a complete ordered part table before provider completion."""
    if [part.part_number for part in parts] != list(
        range(1, asset.part_count + 1)
    ):
        raise UploadStateError("upload_state_conflict")
    if sum(part.byte_size for part in parts) != asset.size_bytes:
        raise UploadStateError("upload_state_conflict")


def _descriptor(asset: AssetRecord) -> AssetDescriptor:
    """Project only safe metadata for a completed asset."""
    return build_asset_descriptor(asset)


def _session_for(asset: AssetRecord, bucket_name: str) -> MultipartSession:
    """Reconstruct a provider session without exposing it at the API seam."""
    if asset.obs_upload_id is None:
        raise UploadStateError("upload_state_conflict")
    return MultipartSession(bucket_name, asset.object_key, asset.obs_upload_id)


def _abort_quietly(
    storage: MultipartStorage, session: MultipartSession
) -> bool:
    """Best-effort cleanup after a provider session loses its DB binding."""
    try:
        storage.abort(session)
    except MultipartStorageError:
        return False
    except (ConnectionError, OSError, TimeoutError):
        return False
    return True


def _discard_quietly(
    registry: ResumableUploadRegistry,
    asset: AssetRecord,
) -> bool:
    """Best-effort discard without replacing the original stable error."""
    try:
        return registry.discard_unbound_allocation(
            asset.asset_id,
            owner=asset.owner_subject,
        )
    except (sqlite3.Error, OSError):
        return False


def _utc_now() -> datetime:
    """Return the service clock's default timezone-aware value."""
    return datetime.now(UTC)
