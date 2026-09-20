# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded multipart storage ports for the Bot-owned OBS boundary."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple, Never, Protocol, TypeVar

from ..runtime.outbound import ObsClientRuntime, ObsProfileName
from .obs_storage import normalize_obs_object_key

__all__ = [
    "BoundedMultipartStorage",
    "CompletedObject",
    "CompletedObjectReader",
    "FakeMultipartStorage",
    "PartInput",
    "MultipartSession",
    "MultipartStorage",
    "MultipartStorageError",
    "StoredPart",
]

_READ_CHUNK_BYTES = 1024 * 1024
T = TypeVar("T")


class MultipartStorageError(OSError):
    """Storage failure with a stable code and no provider payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MultipartSession(NamedTuple):
    """Internal upload session coordinates."""

    bucket: str
    object_key: str
    upload_id: str


class StoredPart(NamedTuple):
    """Internal part result retained by the upload registry."""

    part_number: int
    etag: str
    byte_size: int
    sha256: str


class PartInput(NamedTuple):
    """One bounded part body and its caller-validated metadata."""

    part_number: int
    source: BinaryIO
    content_length: int
    sha256: str


class CompletedObject(NamedTuple):
    """Safe internal result of a successful object completion."""

    bucket: str
    object_key: str
    byte_size: int


def _multipart_protocol_member() -> Never:
    """Mark an unimplemented structural storage member as unreachable."""
    raise NotImplementedError


class MultipartStorage(Protocol):
    """Port consumed by the resumable upload application service."""

    def begin(
        self,
        *,
        bucket: str,
        object_key: str,
    ) -> MultipartSession:
        """Start one provider multipart session."""
        _multipart_protocol_member()

    def put_part(
        self,
        session: MultipartSession,
        upload: PartInput,
    ) -> StoredPart:
        """Stream one bounded part and return its internal ETag."""
        _multipart_protocol_member()

    def complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject:
        """Complete the provider upload from authoritative parts."""
        _multipart_protocol_member()

    def abort(self, session: MultipartSession) -> None:
        """Abort one unfinished provider session."""
        _multipart_protocol_member()

    def reconcile_complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject | None:
        """Find a successful completion after an unknown provider outcome."""
        _multipart_protocol_member()

    def download_to_path(
        self,
        *,
        bucket: str,
        object_key: str,
        destination: Path,
        expected_size: int,
    ) -> int:
        """Stream one completed object into a caller-owned file."""
        _multipart_protocol_member()


CompletedObjectReader = Callable[..., int]


class BoundedMultipartStorage:
    """Huawei OBS multipart adapter that never buffers a complete asset."""

    def __init__(
        self,
        *,
        runtime: ObsClientRuntime,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._runtime = runtime
        self._loop = loop

    def begin(self, *, bucket: str, object_key: str) -> MultipartSession:
        """Initiate a provider multipart upload for a safe object key."""
        safe_key = normalize_obs_object_key(object_key, bucket)
        try:
            response = self._run(
                lambda client: client.initiateMultipartUpload(
                    bucketName=bucket,
                    objectKey=safe_key,
                    contentType="application/octet-stream",
                )
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise MultipartStorageError("upload_storage_unavailable") from exc
        _require_ok(response)
        upload_id = getattr(getattr(response, "body", None), "uploadId", None)
        if not isinstance(upload_id, str) or not upload_id:
            raise MultipartStorageError("upload_storage_unavailable")
        return MultipartSession(bucket, safe_key, upload_id)

    def put_part(
        self,
        session: MultipartSession,
        upload: PartInput,
    ) -> StoredPart:
        """Send a file-like part with exact SDK content length."""
        _rewind(upload.source)
        response = self._run(
            lambda client: client.uploadPart(
                bucketName=session.bucket,
                objectKey=session.object_key,
                partNumber=upload.part_number,
                uploadId=session.upload_id,
                content=upload.source,
                partSize=upload.content_length,
                autoClose=False,
            )
        )
        _require_ok(response)
        etag = getattr(getattr(response, "body", None), "etag", None)
        if not isinstance(etag, str) or not etag:
            raise MultipartStorageError("upload_storage_unavailable")
        return StoredPart(
            upload.part_number,
            etag,
            upload.content_length,
            upload.sha256,
        )

    def complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject:
        """Complete from the registry-owned ordered ETag list."""
        obs_model: Any = import_module("obs.model")
        request = obs_model.CompleteMultipartUploadRequest(
            parts=[
                obs_model.CompletePart(
                    partNum=part.part_number, etag=part.etag
                )
                for part in parts
            ]
        )
        try:
            response = self._run(
                lambda client: client.completeMultipartUpload(
                    bucketName=session.bucket,
                    objectKey=session.object_key,
                    uploadId=session.upload_id,
                    completeMultipartUploadRequest=request,
                )
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            if _looks_unknown(exc):
                reconciled = self.reconcile_complete(session, parts)
                if reconciled is not None:
                    return reconciled
                raise MultipartStorageError("obs_outcome_unknown") from exc
            raise MultipartStorageError("upload_storage_unavailable") from exc
        _require_ok(response)
        return CompletedObject(
            session.bucket,
            session.object_key,
            sum(part.byte_size for part in parts),
        )

    def abort(self, session: MultipartSession) -> None:
        """Abort a provider upload and map provider details to one code."""
        try:
            response = self._run(
                lambda client: client.abortMultipartUpload(
                    bucketName=session.bucket,
                    objectKey=session.object_key,
                    uploadId=session.upload_id,
                )
            )
            _require_ok(response)
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise MultipartStorageError("upload_storage_unavailable") from exc

    def reconcile_complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject | None:
        """Check object metadata after an ambiguous complete response."""
        try:
            response = self._run(
                lambda client: client.getObjectMetadata(
                    bucketName=session.bucket,
                    objectKey=session.object_key,
                )
            )
        except (ConnectionError, OSError, TimeoutError):
            return None
        if getattr(response, "status", 500) >= 300:
            return None
        body = getattr(response, "body", None)
        byte_size = getattr(body, "contentLength", None)
        if not isinstance(byte_size, int):
            return None
        expected = sum(part.byte_size for part in parts)
        if byte_size != expected:
            return None
        return CompletedObject(session.bucket, session.object_key, byte_size)

    def download_to_path(
        self,
        *,
        bucket: str,
        object_key: str,
        destination: Path,
        expected_size: int,
    ) -> int:
        """Download one completed object without retaining its bytes."""
        safe_key = normalize_obs_object_key(object_key, bucket)
        response = self._run(
            lambda client: client.downloadFile(
                bucketName=bucket,
                objectKey=safe_key,
                downloadFile=str(destination),
                partSize=128 * 1024**2,
                taskNum=1,
                enableCheckpoint=False,
            )
        )
        _require_ok(response)
        try:
            actual_size = destination.stat().st_size
        except OSError as error:
            raise MultipartStorageError(
                "upload_storage_unavailable"
            ) from error
        if actual_size != expected_size:
            raise MultipartStorageError("upload_state_conflict")
        return actual_size

    def _run(self, operation: Callable[[Any], T]) -> T:
        """Run one SDK operation on the owning event loop and OBS runtime."""
        future: Future[T] = asyncio.run_coroutine_threadsafe(
            self._runtime.run(ObsProfileName.PRIMARY, operation), self._loop
        )
        return future.result()


@dataclass
class _FakeSessionState:
    """Provider-free per-session state for bounded storage tests."""

    session: MultipartSession
    parts: dict[int, bytes]
    completed: bool = False
    aborted: bool = False


class FakeMultipartStorage:
    """Deterministic multipart provider that retains one part at a time."""

    def __init__(self) -> None:
        self.sessions: dict[str, _FakeSessionState] = {}
        self.read_sizes: list[int] = []
        self.fail_complete_unknown = False

    def begin(self, *, bucket: str, object_key: str) -> MultipartSession:
        """Create one fake session and return opaque coordinates."""
        session = MultipartSession(bucket, object_key, secrets.token_hex(8))
        self.sessions[session.upload_id] = _FakeSessionState(session, {})
        return session

    def put_part(
        self,
        session: MultipartSession,
        upload: PartInput,
    ) -> StoredPart:
        """Read one part in fixed chunks and verify its digest."""
        state = self._state(session)
        digest, content = _read_part(
            upload.source,
            content_length=upload.content_length,
            expected_sha256=upload.sha256,
            read_sizes=self.read_sizes,
        )
        state.parts[upload.part_number] = content
        return StoredPart(
            upload.part_number,
            f"etag-{upload.part_number}-{digest[:12]}",
            upload.content_length,
            digest,
        )

    def complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject:
        """Complete the fake session or simulate an ambiguous outcome."""
        state = self._state(session)
        if state.aborted:
            raise MultipartStorageError("upload_state_conflict")
        if self.fail_complete_unknown:
            state.completed = True
            raise MultipartStorageError("obs_outcome_unknown")
        expected = [part.part_number for part in parts]
        if expected != sorted(state.parts):
            raise MultipartStorageError("upload_state_conflict")
        state.completed = True
        return CompletedObject(
            session.bucket,
            session.object_key,
            sum(part.byte_size for part in parts),
        )

    def abort(self, session: MultipartSession) -> None:
        """Mark one fake session aborted idempotently."""
        state = self._state(session)
        if state.completed:
            return
        state.aborted = True

    def reconcile_complete(
        self,
        session: MultipartSession,
        parts: Sequence[StoredPart],
    ) -> CompletedObject | None:
        """Reconcile the fake's completed marker after an unknown result."""
        state = self._state(session)
        if not state.completed:
            return None
        return CompletedObject(
            session.bucket,
            session.object_key,
            sum(part.byte_size for part in parts),
        )

    def download_to_path(
        self,
        *,
        bucket: str,
        object_key: str,
        destination: Path,
        expected_size: int,
    ) -> int:
        """Write completed fake parts one at a time for resolver tests."""
        state = next(
            (
                candidate
                for candidate in self.sessions.values()
                if candidate.session.bucket == bucket
                and candidate.session.object_key == object_key
            ),
            None,
        )
        if state is None or not state.completed:
            raise MultipartStorageError("upload_asset_not_found")
        total = 0
        try:
            with destination.open("wb") as output:
                for part_number in sorted(state.parts):
                    content = state.parts[part_number]
                    total += len(content)
                    if total > expected_size:
                        raise MultipartStorageError("upload_state_conflict")
                    output.write(content)
        except OSError as error:
            raise MultipartStorageError(
                "upload_storage_unavailable"
            ) from error
        if total != expected_size:
            raise MultipartStorageError("upload_state_conflict")
        return total

    def _state(self, session: MultipartSession) -> _FakeSessionState:
        """Resolve a fake session or return a sanitized storage error."""
        state = self.sessions.get(session.upload_id)
        if state is None:
            raise MultipartStorageError("upload_asset_not_found")
        return state


def _read_part(
    source: BinaryIO,
    *,
    content_length: int,
    expected_sha256: str,
    read_sizes: list[int] | None = None,
) -> tuple[str, bytes]:
    """Read exactly one bounded part and return its digest plus test bytes."""
    if content_length < 0:
        raise MultipartStorageError("invalid_upload_metadata")
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    while total < content_length:
        block = source.read(min(_READ_CHUNK_BYTES, content_length - total))
        if not block:
            raise MultipartStorageError("upload_state_conflict")
        total += len(block)
        if total > content_length:
            raise MultipartStorageError("upload_state_conflict")
        digest.update(block)
        chunks.append(block)
        if read_sizes is not None:
            read_sizes.append(len(block))
    extra = source.read(1)
    if extra:
        raise MultipartStorageError("upload_state_conflict")
    actual = digest.hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise MultipartStorageError("upload_checksum_mismatch")
    return actual, b"".join(chunks)


def _rewind(source: BinaryIO) -> None:
    """Rewind one bounded part before handing it to the SDK."""
    try:
        source.seek(0)
    except (OSError, ValueError) as exc:
        raise MultipartStorageError("upload_storage_unavailable") from exc


def _require_ok(response: Any) -> None:
    """Raise one sanitized storage code for a non-success SDK response."""
    status = getattr(response, "status", 500)
    if status >= 300:
        raise MultipartStorageError("upload_storage_unavailable")


def _looks_unknown(exc: BaseException) -> bool:
    """Classify transport failures where completion may have committed."""
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))
