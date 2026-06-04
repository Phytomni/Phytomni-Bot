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

import tempfile
from pathlib import Path
from typing import Any

from obs import ObsClient

from ..config.settings import get_sensitive_config
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
    "list_object_keys",
]

_LIST_MAX_KEYS = 1000


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


def get_object_bytes(
    bucket: str,
    object_key: str,
    *,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> bytes:
    """Return the bytes of one object by its exact key.

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
    safe_key = normalize_obs_object_key(object_key, bucket)

    def _obsfs() -> bytes:
        return obsfs_path_for(safe_key, bucket, mount_root).read_bytes()

    def _sdk() -> bytes:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = handle.name
        try:
            response = _obs_client(obs_server).getObject(
                bucketName=bucket, objectKey=safe_key, downloadPath=temp_path
            )
            _require_ok(response, "download")
            return Path(temp_path).read_bytes()
        finally:
            Path(temp_path).unlink(missing_ok=True)

    return obsfs_or_sdk(_obsfs, _sdk)


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
