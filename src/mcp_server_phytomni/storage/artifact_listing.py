# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""List object paths under a terminal run's OBS output directory.

Prefers the obsfs mount (a recursive directory walk, no credentials)
and falls back to the OBS SDK list when the bucket or the specific run
directory is not mounted. The obsfs-vs-SDK choice is made EAGERLY
(``obsfs_bucket_available`` + ``is_dir``), never through a lazy
``obsfs_or_sdk`` generator, because a directory walk cannot fall back
mid-iteration once it has started yielding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_server_phytomni.runtime.outbound import ObsProfileName
from mcp_server_phytomni.storage.obs_relay_ops import (
    ObsAccessOptions,
    list_object_keys,
    list_object_keys_page,
    object_size,
)
from mcp_server_phytomni.storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    normalize_obs_object_key,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_path_for,
)

__all__ = [
    "ListedArtifactObject",
    "list_artifact_objects",
    "list_artifact_objects_with_runtime",
    "list_artifact_paths",
    "list_artifact_paths_with_runtime",
]


@dataclass(frozen=True, slots=True)
class ListedArtifactObject:
    """One output object with actual size and a safe download reference."""

    relative_path: str
    source_path: str
    size_bytes: int
    download_ref: str | None = None


def list_artifact_objects(
    output_dir: str,
    *,
    bucket_name: str,
    client: Any | None = None,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> list[ListedArtifactObject]:
    """List output objects with actual byte sizes.

    The obsfs branch obtains sizes from ``stat`` on the mounted file. The
    SDK branch obtains each size with an object metadata request; no producer
    manifest value is consulted here. All returned references stay under the
    requested output-directory prefix.
    """
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    base_key = object_key.rstrip("/")
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            return _list_obsfs_objects(
                dir_path,
                base_key=base_key,
                bucket_name=bucket_name,
            )

    prefix = f"{base_key}/" if base_key else ""
    access = ObsAccessOptions(client=client, mount_root=mount_root)
    return _list_sdk_objects(
        list_object_keys(bucket_name, prefix, access=access),
        base_key=base_key,
        bucket_name=bucket_name,
        access=access,
    )


async def list_artifact_objects_with_runtime(
    output_dir: str,
    *,
    bucket_name: str,
    obs_runtime: Any,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> list[ListedArtifactObject]:
    """List output objects with a separate OBS lease per SDK request."""
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    base_key = object_key.rstrip("/")
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            return _list_obsfs_objects(
                dir_path,
                base_key=base_key,
                bucket_name=bucket_name,
            )
    prefix = f"{base_key}/" if base_key else ""
    keys = await _list_sdk_keys_with_runtime(
        bucket_name,
        prefix,
        obs_runtime=obs_runtime,
        mount_root=mount_root,
    )
    objects: list[ListedArtifactObject] = []
    for key in keys:
        safe_key = normalize_obs_object_key(key, bucket_name)
        relative_path = _relative_output_path(safe_key, base_key)
        if relative_path is None:
            continue
        size_bytes = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client, safe_key=safe_key: object_size(
                bucket_name,
                safe_key,
                access=ObsAccessOptions(
                    client=client,
                    mount_root=mount_root,
                ),
            ),
        )
        download_ref = obs_path_from_key(bucket_name, safe_key)
        objects.append(
            ListedArtifactObject(
                relative_path=relative_path,
                source_path=download_ref,
                size_bytes=size_bytes,
                download_ref=download_ref,
            )
        )
    return objects


def _list_obsfs_objects(
    directory: Path,
    *,
    base_key: str,
    bucket_name: str,
) -> list[ListedArtifactObject]:
    """Build object records from one confined obsfs directory."""
    dir_path = directory
    resolved_dir_path = dir_path.resolve()
    objects: list[ListedArtifactObject] = []
    for path in sorted(dir_path.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_dir_path)
        except ValueError:
            continue
        relative_path = path.relative_to(dir_path).as_posix()
        object_key = (
            f"{base_key}/{relative_path}" if base_key else relative_path
        )
        download_ref = obs_path_from_key(bucket_name, object_key)
        objects.append(
            ListedArtifactObject(
                relative_path=relative_path,
                source_path=str(path),
                size_bytes=resolved_path.stat().st_size,
                download_ref=download_ref,
            )
        )
    return objects


def _list_sdk_objects(
    keys: list[str],
    *,
    base_key: str,
    bucket_name: str,
    access: ObsAccessOptions,
) -> list[ListedArtifactObject]:
    """Build object records from SDK keys after prefix confinement."""
    objects: list[ListedArtifactObject] = []
    for key in keys:
        safe_key = normalize_obs_object_key(key, bucket_name)
        relative_path = _relative_output_path(safe_key, base_key)
        if relative_path is None:
            continue
        download_ref = obs_path_from_key(bucket_name, safe_key)
        objects.append(
            ListedArtifactObject(
                relative_path=relative_path,
                source_path=download_ref,
                size_bytes=object_size(
                    bucket_name,
                    safe_key,
                    access=access,
                ),
                download_ref=download_ref,
            )
        )
    return objects


def _relative_output_path(key: str, base_key: str) -> str | None:
    """Return a key's relative path only when it is under base_key."""
    if base_key:
        prefix = f"{base_key}/"
        if not key.startswith(prefix):
            return None
        relative_path = key.removeprefix(prefix)
    else:
        relative_path = key
    return relative_path or None


def list_artifact_paths(
    output_dir: str,
    *,
    bucket_name: str,
    client: Any | None = None,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> list[str]:
    """Return public ``/obs/<bucket>/<key>`` paths of files under output_dir.

    Args:
        output_dir: OBS-style directory path written by the run.
        bucket_name: OBS bucket the run wrote to.
        client: Runtime-owned OBS client for the SDK fallback.
        mount_root: obsfs mount root (default ``/obs``).

    Returns:
        Public paths of every file under ``output_dir`` (directories
        excluded), or an empty list when nothing is found.
    """
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            rels = [
                p.relative_to(dir_path).as_posix()
                for p in sorted(dir_path.rglob("*"))
                if p.is_file()
            ]
            return [
                obs_path_from_key(
                    bucket_name,
                    f"{object_key}/{rel}" if object_key else rel,
                )
                for rel in rels
            ]
    keys = list_object_keys(
        bucket_name,
        object_key,
        access=ObsAccessOptions(client=client, mount_root=mount_root),
    )
    return [obs_path_from_key(bucket_name, key) for key in keys]


async def list_artifact_paths_with_runtime(
    output_dir: str,
    *,
    bucket_name: str,
    obs_runtime: Any,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> list[str]:
    """List output paths with a separate OBS lease for each list page."""
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            rels = [
                path.relative_to(dir_path).as_posix()
                for path in sorted(dir_path.rglob("*"))
                if path.is_file()
            ]
            return [
                obs_path_from_key(
                    bucket_name,
                    f"{object_key}/{relative}" if object_key else relative,
                )
                for relative in rels
            ]
    keys = await _list_sdk_keys_with_runtime(
        bucket_name,
        object_key,
        obs_runtime=obs_runtime,
        mount_root=mount_root,
    )
    return [obs_path_from_key(bucket_name, key) for key in keys]


async def _list_sdk_keys_with_runtime(
    bucket_name: str,
    prefix: str,
    *,
    obs_runtime: Any,
    mount_root: str,
) -> list[str]:
    """Fetch all list pages through individually scoped SDK operations."""
    keys: list[str] = []
    marker: str | None = None
    while True:
        page, marker = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client: list_object_keys_page(
                bucket_name,
                prefix,
                marker,
                access=ObsAccessOptions(
                    client=client,
                    mount_root=mount_root,
                ),
            ),
        )
        keys.extend(page)
        if marker is None:
            return keys
