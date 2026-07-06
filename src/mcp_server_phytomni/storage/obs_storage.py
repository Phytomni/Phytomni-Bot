# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS path helpers for obsfs-first storage operations.

Classes: ObsPathError.
Functions: normalize_obs_object_key, obsfs_bucket_root, obsfs_path_for,
    obsfs_bucket_available, obsfs_path_exists, obs_path_from_key,
    bucket_colon_path, obsfs_or_sdk.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

DEFAULT_OBSFS_MOUNT_ROOT = "/obs"
OBSFS_FALLBACK_ERRORS = (OSError,)


class ObsPathError(ValueError):
    """Raised when an OBS-style path cannot be safely mapped to obsfs."""


def normalize_obs_object_key(obs_path: str, bucket_name: str) -> str:
    """Return a safe object key for accepted OBS path formats.

    Args:
        obs_path: OBS path in various formats
            (obs://, bucket:/, /obs/bucket/, /bucket/).
        bucket_name: Target OBS bucket name.

    Returns:
        str: Normalized object key string safe for OBS SDK usage.

    Raises:
        ObsPathError: If path points outside the specified bucket.
    """
    path_value = str(obs_path)
    bucket = _safe_bucket_name(bucket_name)

    if path_value.startswith("obs://"):
        path_value = _strip_obs_scheme(path_value, bucket)
    elif path_value.startswith(f"{bucket}:/"):
        path_value = path_value[len(f"{bucket}:/") :]
    elif path_value.startswith(f"/obs/{bucket}/"):
        path_value = path_value[len(f"/obs/{bucket}/") :]
    elif path_value == f"/obs/{bucket}":
        path_value = ""
    elif path_value.startswith("/obs/"):
        raise ObsPathError(
            f"OBS path points outside bucket '{bucket}': {obs_path}"
        )
    elif path_value.startswith(f"/{bucket}/"):
        path_value = path_value[len(f"/{bucket}/") :]
    elif path_value == f"/{bucket}":
        path_value = ""
    elif path_value.startswith("/"):
        path_value = path_value[1:]

    return _safe_object_key(path_value)


def obsfs_bucket_root(
    bucket_name: str,
    mount_root: str | Path = DEFAULT_OBSFS_MOUNT_ROOT,
) -> Path:
    """Return the obsfs root path for one bucket.

    Args:
        bucket_name: OBS bucket name.
        mount_root: Root path for obsfs mount (default /obs).

    Returns:
        Path: Full obsfs path to the bucket root directory.
    """
    return Path(mount_root) / _safe_bucket_name(bucket_name)


def obsfs_path_for(
    obs_path: str,
    bucket_name: str,
    mount_root: str | Path = DEFAULT_OBSFS_MOUNT_ROOT,
) -> Path:
    """Return the obsfs filesystem path for an OBS-style path.

    Args:
        obs_path: OBS-style path (obs://, bucket:/, /obs/bucket/, etc.).
        bucket_name: OBS bucket name.
        mount_root: Root path for obsfs mount (default /obs).

    Returns:
        Path: Full obsfs filesystem path for the OBS object.
    """
    object_key = normalize_obs_object_key(obs_path, bucket_name)
    root = obsfs_bucket_root(bucket_name, mount_root)
    if not object_key:
        return root
    return root.joinpath(*object_key.split("/"))


def obsfs_bucket_available(
    bucket_name: str,
    mount_root: str | Path = DEFAULT_OBSFS_MOUNT_ROOT,
) -> bool:
    """Return whether the obsfs bucket root is currently readable.

    Args:
        bucket_name: OBS bucket name.
        mount_root: Root path for obsfs mount (default /obs).

    Returns:
        bool: True if bucket root is a readable directory, False otherwise.
    """
    root = obsfs_bucket_root(bucket_name, mount_root)
    try:
        return root.is_dir()
    except OSError:
        return False


def obsfs_path_exists(
    obs_path: str,
    bucket_name: str,
    mount_root: str | Path = DEFAULT_OBSFS_MOUNT_ROOT,
) -> bool:
    """Return whether one OBS path exists through obsfs.

    Args:
        obs_path: OBS-style path to check.
        bucket_name: OBS bucket name.
        mount_root: Root path for obsfs mount (default /obs).

    Returns:
        bool: True if the path exists as a file through obsfs, False otherwise.
    """
    try:
        return obsfs_path_for(obs_path, bucket_name, mount_root).exists()
    except OSError:
        return False


def obs_path_from_key(bucket_name: str, object_key: str) -> str:
    """Return the public `/obs/<bucket>/<key>` path for an object key.

    Args:
        bucket_name: OBS bucket name.
        object_key: Object key within the bucket.

    Returns:
        str: Public OBS path in format /obs/<bucket>/<key>.
    """
    safe_key = normalize_obs_object_key(object_key, bucket_name)
    if not safe_key:
        return f"/obs/{_safe_bucket_name(bucket_name)}"
    return f"/obs/{_safe_bucket_name(bucket_name)}/{safe_key}"


def bucket_colon_path(bucket_name: str, object_key: str) -> str:
    """Return the legacy `<bucket>:/<key>` path for an object key.

    Args:
        bucket_name: OBS bucket name.
        object_key: Object key within the bucket.

    Returns:
        str: Legacy path in format <bucket>:/<key>.
    """
    safe_key = normalize_obs_object_key(object_key, bucket_name)
    return f"{_safe_bucket_name(bucket_name)}:/{safe_key}"


def obsfs_or_sdk[T](
    obsfs_action: Callable[[], T],
    sdk_action: Callable[[], T],
) -> T:
    """Run an obsfs action and fall back to the SDK action on I/O errors.

    Args:
        obsfs_action: Callable to execute via obsfs mount.
        sdk_action: Callable to execute via OBS SDK if obsfs fails.

    Returns:
        T: Result from whichever action succeeded.
    """
    try:
        return obsfs_action()
    except OBSFS_FALLBACK_ERRORS:
        return sdk_action()


def _strip_obs_scheme(path_value: str, bucket_name: str) -> str:
    """Strip the obs:// scheme and optional bucket prefix."""
    without_scheme = path_value[len("obs://") :]
    if without_scheme == bucket_name:
        return ""
    if without_scheme.startswith(f"{bucket_name}/"):
        return without_scheme[len(f"{bucket_name}/") :]
    return without_scheme


def _safe_bucket_name(bucket_name: str) -> str:
    """Validate and return a bucket name safe for local path joining."""
    bucket = str(bucket_name).strip("/")
    if not bucket or "/" in bucket or bucket in {".", ".."}:
        raise ObsPathError(f"Invalid OBS bucket name: {bucket_name}")
    return bucket


def _safe_object_key(object_key: str) -> str:
    """Normalize an object key without allowing parent traversal."""
    key = object_key.replace("\\", "/")
    trailing_slash = key.endswith("/")
    raw_parts = PurePosixPath(key).parts
    parts = [part for part in raw_parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ObsPathError(f"OBS object key escapes bucket root: {object_key}")
    safe_key = "/".join(parts)
    if safe_key and trailing_slash:
        return f"{safe_key}/"
    return safe_key
