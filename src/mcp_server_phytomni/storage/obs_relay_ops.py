# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Server-side key-exact OBS object operations for relay SDK termination.

Functions: put_object_bytes, put_dir_marker, get_object_bytes,
list_object_keys. The operator relay terminates a customer relay-mode
OBS call by running these against its AK/SK ``ObsClient`` (the child Bot
holds none); each re-validates the client path through
``normalize_obs_object_key`` so a compromised child cannot escape the
bucket, preferring the obsfs mount with an SDK fallback.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from .obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    normalize_obs_object_key,
    obsfs_bucket_available,
    obsfs_or_sdk,
    obsfs_path_for,
)

__all__ = [
    "ObsObjectAlreadyExistsError",
    "ObsAccessOptions",
    "ObsObjectMetadataError",
    "ObsObjectNotFoundError",
    "ObsStreamOptions",
    "head_object_metadata",
    "put_object_bytes",
    "put_object_bytes_if_absent",
    "put_object_file",
    "put_dir_marker",
    "get_object_bytes",
    "object_size",
    "iter_object_chunks",
    "list_object_keys",
]

_LIST_MAX_KEYS = 1000
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class ObsObjectNotFoundError(FileNotFoundError):
    """Raised when a requested OBS object is confirmed absent."""


class ObsObjectAlreadyExistsError(FileExistsError):
    """Raised when a conditional OBS object creation finds an existing key."""


class ObsObjectMetadataError(OSError):
    """Raised when an exact-key OBS metadata read cannot be trusted."""


@dataclass(frozen=True, slots=True)
class _ObsObjectMetadata:
    """Private exact-key metadata returned without object body access."""

    size_bytes: int
    etag: str | None
    version_id: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class ObsAccessOptions:
    """Transport context shared by one operator OBS operation."""

    client: Any | None = None
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT


@dataclass(frozen=True, slots=True)
class ObsStreamOptions:
    """Controls for one bounded OBS object stream."""

    chunk_size: int = _DOWNLOAD_CHUNK_BYTES
    on_source_open: Any | None = None
    stop: Any | None = None


@dataclass(frozen=True, slots=True)
class _SdkChunkOptions:
    """Streaming controls passed to the synchronous SDK iterator."""

    access: ObsAccessOptions
    stream: ObsStreamOptions


def _resolve_access(access: ObsAccessOptions | None) -> ObsAccessOptions:
    """Return one explicit access context for an OBS operation."""
    return access or ObsAccessOptions()


def head_object_metadata(
    bucket: str, object_key: str, *, client: Any
) -> _ObsObjectMetadata:
    """Read one already-normalized object's metadata through SDK HEAD only.

    This low-level adapter deliberately accepts the configured bucket and
    normalized object key as-is.  Its callers own path normalization and can
    reuse one client for a batch of exact-key HEAD requests.  It never calls
    list, download, write, or delete SDK methods.

    Args:
        bucket: Configured OBS bucket name, already validated by the caller.
        object_key: Normalized key within ``bucket``.
        client: Operator-authenticated OBS SDK client.

    Returns:
        Private immutable metadata for the exact object key.

    Raises:
        ObsObjectNotFoundError: If the SDK confirms that the key is absent.
        ObsObjectMetadataError: If the metadata response is invalid or fails.
    """
    try:
        response = client.getObjectMetadata(
            bucketName=bucket, objectKey=object_key
        )
        _require_ok(response, "head")
        body = response.body
        size_bytes = int(body.contentLength)
        if size_bytes < 0:
            raise ValueError("negative content length")
        return _ObsObjectMetadata(
            size_bytes=size_bytes,
            etag=_optional_metadata_value(body, "etag"),
            version_id=_optional_metadata_value(body, "versionId"),
            last_modified=_optional_metadata_value(body, "lastModified"),
        )
    except ObsObjectNotFoundError:
        raise
    except Exception:
        raise ObsObjectMetadataError(
            "OBS object metadata is unavailable"
        ) from None


def _optional_metadata_value(body: Any, attribute: str) -> str | None:
    """Return an SDK metadata value as text, preserving missing as ``None``."""
    value = getattr(body, attribute, None)
    return None if value is None else str(value)


def _require_ok(response: Any, action: str) -> None:
    """Raise ``OSError`` when an OBS SDK response is missing or >= 300."""
    status = getattr(response, "status", None)
    if status is None or status >= 300:
        if status == 404 and action in {"download", "head"}:
            raise ObsObjectNotFoundError("OBS object not found")
        if action == "conditional upload" and status in {409, 412}:
            raise ObsObjectAlreadyExistsError("OBS object already exists")
        raise OSError(
            f"OBS {action} failed: "
            f"requestId={getattr(response, 'requestId', 'unknown')} "
            f"errorCode={getattr(response, 'errorCode', 'unknown')} "
            f"errorMessage={getattr(response, 'errorMessage', 'unknown')}"
        )


def _require_mount(bucket: str, mount_root: str) -> None:
    """Raise ``FileNotFoundError`` when the obsfs bucket is not mounted."""
    if not obsfs_bucket_available(bucket, mount_root):
        raise FileNotFoundError(
            f"OBSFS bucket is not available: {mount_root}/{bucket}"
        )


def put_object_bytes(
    bucket: str,
    object_key: str,
    content: bytes,
    *,
    access: ObsAccessOptions | None = None,
) -> str:
    """Write ``content`` at the exact ``object_key`` and return its key.

    Args:
        bucket: Target OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any write.
        content: Raw object bytes.
        access: OBS endpoint, client, and obsfs mount context.

    Returns:
        The normalized object key that was written.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs write and the SDK fallback fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)

    def _obsfs() -> None:
        _require_mount(bucket, resolved_access.mount_root)
        destination = obsfs_path_for(
            safe_key, bucket, resolved_access.mount_root
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def _sdk() -> None:
        response = _resolve_client(resolved_access.client).putContent(
            bucketName=bucket, objectKey=safe_key, content=content
        )
        _require_ok(response, "upload")

    obsfs_or_sdk(_obsfs, _sdk)
    return safe_key


def put_object_bytes_if_absent(
    bucket: str,
    object_key: str,
    content: bytes,
    *,
    access: ObsAccessOptions | None = None,
) -> str:
    """Create an object only when its key is absent, without overwriting.

    The obsfs path uses exclusive creation. The SDK path sends the standard
    conditional request header so a concurrent writer receives a stable
    ``ObsObjectAlreadyExistsError`` instead of replacing private inventory.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)

    def _obsfs() -> None:
        _require_mount(bucket, resolved_access.mount_root)
        destination = obsfs_path_for(
            safe_key, bucket, resolved_access.mount_root
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            raise ObsObjectAlreadyExistsError(
                "OBS object already exists"
            ) from None

    def _sdk() -> None:
        response = _resolve_client(resolved_access.client).putContent(
            bucketName=bucket,
            objectKey=safe_key,
            content=content,
            extensionHeaders={"If-None-Match": "*"},
        )
        _require_ok(response, "conditional upload")

    if not obsfs_bucket_available(bucket, resolved_access.mount_root):
        _sdk()
    else:
        try:
            _obsfs()
        except ObsObjectAlreadyExistsError:
            raise
        except OSError:
            _sdk()
    return safe_key


def put_object_file(
    bucket: str,
    object_key: str,
    source: Path,
    *,
    access: ObsAccessOptions | None = None,
) -> str:
    """Upload one local file at the exact object key without buffering it.

    The obsfs path delegates byte-for-byte copying to ``shutil.copyfile``;
    the SDK fallback delegates file streaming to the OBS client.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)

    def _obsfs() -> None:
        _require_mount(bucket, resolved_access.mount_root)
        destination = obsfs_path_for(
            safe_key, bucket, resolved_access.mount_root
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def _sdk() -> None:
        response = _resolve_client(resolved_access.client).putFile(
            bucketName=bucket,
            objectKey=safe_key,
            file_path=str(source),
        )
        _require_ok(response, "upload")

    obsfs_or_sdk(_obsfs, _sdk)
    return safe_key


def put_dir_marker(
    bucket: str,
    object_key: str,
    *,
    access: ObsAccessOptions | None = None,
) -> str:
    """Create a zero-byte directory marker object and return its key.

    Args:
        bucket: Target OBS bucket name.
        object_key: Client-supplied directory key (conventionally
            trailing-slashed); re-validated before any write.
        access: OBS endpoint, client, and obsfs mount context.

    Returns:
        The normalized directory key that was created.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs mkdir and the SDK fallback fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)

    def _obsfs() -> None:
        _require_mount(bucket, resolved_access.mount_root)
        obsfs_path_for(safe_key, bucket, resolved_access.mount_root).mkdir(
            parents=True, exist_ok=True
        )

    def _sdk() -> None:
        response = _resolve_client(resolved_access.client).putContent(
            bucketName=bucket, objectKey=safe_key, content=None
        )
        _require_ok(response, "mkdir")

    obsfs_or_sdk(_obsfs, _sdk)
    return safe_key


def object_size(
    bucket: str,
    object_key: str,
    *,
    access: ObsAccessOptions | None = None,
) -> int:
    """Return the object's byte length for the response-size budget.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before the head request.
        access: OBS endpoint, client, and obsfs mount context.

    Returns:
        The object's content length in bytes.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs stat and the SDK head fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)

    def _obsfs() -> int:
        return (
            obsfs_path_for(safe_key, bucket, resolved_access.mount_root)
            .stat()
            .st_size
        )

    def _sdk() -> int:
        # The obs SDK ships no reliable type info (pyright marks
        # getObject's downloadPath required and does not know
        # getObjectMetadata), so bind the client as Any at the call.
        sdk_client = _resolve_client(resolved_access.client)
        response = sdk_client.getObjectMetadata(
            bucketName=bucket, objectKey=safe_key
        )
        _require_ok(response, "head")
        return int(response.body.contentLength)

    return obsfs_or_sdk(_obsfs, _sdk)


def iter_object_chunks(
    bucket: str,
    object_key: str,
    *,
    access: ObsAccessOptions | None = None,
    stream: ObsStreamOptions | None = None,
) -> Iterator[bytes]:
    """Yield one object's bytes in ``chunk_size`` pieces, never fully buffered.

    The obsfs-vs-SDK branch is decided eagerly (a bucket-availability
    bool check, not a lazy generator), because ``obsfs_or_sdk`` cannot
    fall back from a generator whose I/O error only fires on iteration.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any read.
        access: OBS endpoint, client, and obsfs mount context.
        stream: Bounded stream controls, including chunk size and close hooks.

    Returns:
        An iterator over the object's content chunks.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If the SDK read fails.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    resolved_access = _resolve_access(access)
    resolved_stream = stream or ObsStreamOptions()
    if obsfs_bucket_available(bucket, resolved_access.mount_root):
        source = obsfs_path_for(safe_key, bucket, resolved_access.mount_root)
        if source.is_file():
            return _iter_file_chunks(source, resolved_stream.chunk_size)
    return _iter_sdk_chunks(
        bucket,
        safe_key,
        _SdkChunkOptions(
            access=resolved_access,
            stream=resolved_stream,
        ),
    )


def _iter_file_chunks(source: Path, chunk_size: int) -> Iterator[bytes]:
    """Yield a local obsfs file's bytes in ``chunk_size`` pieces."""
    with source.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                return
            yield block


def _iter_sdk_chunks(
    bucket: str,
    safe_key: str,
    options: _SdkChunkOptions,
) -> Iterator[bytes]:
    """Stream an object through the OBS SDK in ``chunk_size`` pieces."""
    # Bind as Any: the obs SDK ships no reliable type info, so pyright
    # wrongly marks getObject's downloadPath as required.
    sdk_client = _resolve_client(options.access.client)
    response = sdk_client.getObject(
        bucketName=bucket, objectKey=safe_key, loadStreamInMemory=False
    )
    _require_ok(response, "download")
    body_stream = response.body.response
    closed = False
    close_lock = Lock()

    def close_stream() -> None:
        """Close the SDK response body once across worker and caller."""
        nonlocal closed
        with close_lock:
            if closed:
                return
            closed = True
        body_stream.close()

    if options.stream.on_source_open is not None:
        options.stream.on_source_open(close_stream)
    try:
        while True:
            if (
                options.stream.stop is not None
                and options.stream.stop.is_set()
            ):
                return
            block = body_stream.read(options.stream.chunk_size)
            if not block:
                return
            yield block
    finally:
        close_stream()


def get_object_bytes(
    bucket: str,
    object_key: str,
    *,
    access: ObsAccessOptions | None = None,
) -> bytes:
    """Return one object's full bytes by its exact key (buffered).

    Built on :func:`iter_object_chunks`; callers that can stream should
    use that primitive instead so a large object is never fully buffered.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any read.
        access: OBS endpoint, client, and obsfs mount context.

    Returns:
        The raw object content.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs read and the SDK fallback fail.
    """
    return b"".join(
        iter_object_chunks(
            bucket,
            object_key,
            access=access,
        )
    )


def list_object_keys(
    bucket: str,
    prefix: str,
    *,
    access: ObsAccessOptions | None = None,
) -> list[str]:
    """Return non-directory object keys under ``prefix``, paginated.

    Args:
        bucket: Source OBS bucket name.
        prefix: Client-supplied prefix or ``/obs/<bucket>/<prefix>`` path;
            re-validated and normalized before listing.
        access: OBS endpoint and client context.

    Returns:
        Every content key under the prefix that is not a directory marker.

    Raises:
        ObsPathError: If the prefix escapes the bucket.
        OSError: If a list page returns a non-2xx status.
    """
    safe_prefix = normalize_obs_object_key(prefix, bucket)
    resolved_access = _resolve_access(access)
    sdk_client = _resolve_client(resolved_access.client)
    keys: list[str] = []
    marker: Any = None
    while True:
        response = sdk_client.listObjects(
            bucketName=bucket,
            prefix=safe_prefix,
            marker=marker,
            max_keys=_LIST_MAX_KEYS,
        )
        _require_ok(response, "list")
        keys.extend(
            item.key
            for item in response.body.contents
            if not item.key.endswith("/")
        )
        if not response.body.is_truncated:
            return keys
        marker = response.body.next_marker


def _resolve_client(client: Any | None) -> Any:
    """Return the client explicitly lent by the owning runtime."""
    if client is not None:
        return client
    raise RuntimeError("OBS runtime client is unavailable")
