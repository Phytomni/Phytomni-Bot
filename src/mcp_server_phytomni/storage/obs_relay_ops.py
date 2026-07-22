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

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..config.settings import get_sensitive_config
from .obs_client import ObsClient
from .obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    normalize_obs_object_key,
    obsfs_bucket_available,
    obsfs_or_sdk,
    obsfs_path_for,
)

__all__ = [
    "put_object_bytes",
    "put_dir_marker",
    "get_object_bytes",
    "object_size",
    "iter_object_chunks",
    "list_object_keys",
]

_LIST_MAX_KEYS = 1000
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _obs_client(obs_server: str) -> ObsClient:
    """Build an operator-credentialed OBS SDK client for ``obs_server``."""
    access_key, secret_key = get_sensitive_config().obs_credentials()
    return ObsClient(
        access_key_id=access_key,
        secret_access_key=secret_key,
        server=obs_server,
    )


def _require_ok(response: Any, action: str) -> None:
    """Raise ``OSError`` when an OBS SDK response is missing or >= 300."""
    status = getattr(response, "status", None)
    if status is None or status >= 300:
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
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> str:
    """Write ``content`` at the exact ``object_key`` and return its key.

    Args:
        bucket: Target OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any write.
        content: Raw object bytes.
        obs_server: OBS endpoint for the SDK fallback.
        mount_root: Filesystem root for the obsfs mount.

    Returns:
        The normalized object key that was written.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs write and the SDK fallback fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)

    def _obsfs() -> None:
        _require_mount(bucket, mount_root)
        destination = obsfs_path_for(safe_key, bucket, mount_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def _sdk() -> None:
        response = _obs_client(obs_server).putContent(
            bucketName=bucket, objectKey=safe_key, content=content
        )
        _require_ok(response, "upload")

    obsfs_or_sdk(_obsfs, _sdk)
    return safe_key


def put_dir_marker(
    bucket: str,
    object_key: str,
    *,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> str:
    """Create a zero-byte directory marker object and return its key.

    Args:
        bucket: Target OBS bucket name.
        object_key: Client-supplied directory key (conventionally
            trailing-slashed); re-validated before any write.
        obs_server: OBS endpoint for the SDK fallback.
        mount_root: Filesystem root for the obsfs mount.

    Returns:
        The normalized directory key that was created.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs mkdir and the SDK fallback fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)

    def _obsfs() -> None:
        _require_mount(bucket, mount_root)
        obsfs_path_for(safe_key, bucket, mount_root).mkdir(
            parents=True, exist_ok=True
        )

    def _sdk() -> None:
        response = _obs_client(obs_server).putContent(
            bucketName=bucket, objectKey=safe_key, content=None
        )
        _require_ok(response, "mkdir")

    obsfs_or_sdk(_obsfs, _sdk)
    return safe_key


def object_size(
    bucket: str,
    object_key: str,
    *,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> int:
    """Return the object's byte length for the response-size budget.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before the head request.
        obs_server: OBS endpoint for the SDK fallback.
        mount_root: Filesystem root for the obsfs mount.

    Returns:
        The object's content length in bytes.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs stat and the SDK head fail.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)

    def _obsfs() -> int:
        return obsfs_path_for(safe_key, bucket, mount_root).stat().st_size

    def _sdk() -> int:
        # The obs SDK ships no reliable type info (pyright marks
        # getObject's downloadPath required and does not know
        # getObjectMetadata), so bind the client as Any at the call.
        client: Any = _obs_client(obs_server)
        response = client.getObjectMetadata(
            bucketName=bucket, objectKey=safe_key
        )
        _require_ok(response, "head")
        return int(response.body.contentLength)

    return obsfs_or_sdk(_obsfs, _sdk)


def iter_object_chunks(
    bucket: str,
    object_key: str,
    *,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
    chunk_size: int = _DOWNLOAD_CHUNK_BYTES,
) -> Iterator[bytes]:
    """Yield one object's bytes in ``chunk_size`` pieces, never fully buffered.

    The obsfs-vs-SDK branch is decided eagerly (a bucket-availability
    bool check, not a lazy generator), because ``obsfs_or_sdk`` cannot
    fall back from a generator whose I/O error only fires on iteration.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any read.
        obs_server: OBS endpoint for the SDK fallback.
        mount_root: Filesystem root for the obsfs mount.
        chunk_size: Bytes to yield per piece.

    Returns:
        An iterator over the object's content chunks.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If the SDK read fails.
    """
    safe_key = normalize_obs_object_key(object_key, bucket)
    if obsfs_bucket_available(bucket, mount_root):
        source = obsfs_path_for(safe_key, bucket, mount_root)
        if source.is_file():
            return _iter_file_chunks(source, chunk_size)
    return _iter_sdk_chunks(bucket, safe_key, obs_server, chunk_size)


def _iter_file_chunks(source: Path, chunk_size: int) -> Iterator[bytes]:
    """Yield a local obsfs file's bytes in ``chunk_size`` pieces."""
    with source.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                return
            yield block


def _iter_sdk_chunks(
    bucket: str, safe_key: str, obs_server: str, chunk_size: int
) -> Iterator[bytes]:
    """Stream an object through the OBS SDK in ``chunk_size`` pieces."""
    # Bind as Any: the obs SDK ships no reliable type info, so pyright
    # wrongly marks getObject's downloadPath as required.
    client: Any = _obs_client(obs_server)
    response = client.getObject(
        bucketName=bucket, objectKey=safe_key, loadStreamInMemory=False
    )
    _require_ok(response, "download")
    stream = response.body.response
    try:
        while True:
            block = stream.read(chunk_size)
            if not block:
                return
            yield block
    finally:
        stream.close()


def get_object_bytes(
    bucket: str,
    object_key: str,
    *,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> bytes:
    """Return one object's full bytes by its exact key (buffered).

    Built on :func:`iter_object_chunks`; callers that can stream should
    use that primitive instead so a large object is never fully buffered.

    Args:
        bucket: Source OBS bucket name.
        object_key: Client-supplied object key or ``/obs/<bucket>/<key>``
            path; re-validated and normalized before any read.
        obs_server: OBS endpoint for the SDK fallback.
        mount_root: Filesystem root for the obsfs mount.

    Returns:
        The raw object content.

    Raises:
        ObsPathError: If the path escapes the bucket.
        OSError: If both the obsfs read and the SDK fallback fail.
    """
    return b"".join(
        iter_object_chunks(
            bucket, object_key, obs_server=obs_server, mount_root=mount_root
        )
    )


def list_object_keys(
    bucket: str,
    prefix: str,
    *,
    obs_server: str,
) -> list[str]:
    """Return non-directory object keys under ``prefix``, paginated.

    Args:
        bucket: Source OBS bucket name.
        prefix: Client-supplied prefix or ``/obs/<bucket>/<prefix>`` path;
            re-validated and normalized before listing.
        obs_server: OBS endpoint for the SDK client.

    Returns:
        Every content key under the prefix that is not a directory marker.

    Raises:
        ObsPathError: If the prefix escapes the bucket.
        OSError: If a list page returns a non-2xx status.
    """
    safe_prefix = normalize_obs_object_key(prefix, bucket)
    client = _obs_client(obs_server)
    keys: list[str] = []
    marker: Any = None
    while True:
        response = client.listObjects(
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
