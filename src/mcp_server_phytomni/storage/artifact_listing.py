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

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_server_phytomni.runtime.outbound import ObsProfileName
from mcp_server_phytomni.storage.obs_relay_ops import (
    ObsAccessOptions,
    ObsListedObject,
    list_object_keys,
    list_object_keys_page,
    list_object_metadata_page,
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

_ARTIFACT_MANIFEST_NAME = ".phytomni-artifacts.json"


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
    limit: int | None = None,
) -> list[ListedArtifactObject]:
    """List output objects with actual byte sizes.

    The obsfs branch obtains sizes from ``stat`` on the mounted file. The
    SDK branch obtains each size with an object metadata request; no producer
    manifest value is consulted here. All returned references stay under the
    requested output-directory prefix. ``limit`` stops the walk after that
    many files so harvest does not HEAD leftover objects in a dirty prefix.
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
                limit=limit,
            )

    prefix = f"{base_key}/" if base_key else ""
    access = ObsAccessOptions(client=client, mount_root=mount_root)
    return _list_sdk_objects(
        _list_sdk_keys(
            bucket_name,
            prefix,
            access=access,
            limit=limit,
        ),
        base_key=base_key,
        bucket_name=bucket_name,
        access=access,
        limit=limit,
    )


async def list_artifact_objects_with_runtime(
    output_dir: str,
    *,
    bucket_name: str,
    obs_runtime: Any,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
    limit: int | None = None,
) -> list[ListedArtifactObject]:
    """List output objects with a separate OBS lease per SDK request."""
    if limit is not None and limit < 1:
        raise ValueError("artifact object limit must be positive")
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    base_key = object_key.rstrip("/")
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            mounted_objects = _list_obsfs_objects(
                dir_path,
                base_key=base_key,
                bucket_name=bucket_name,
                limit=limit,
                preferred_relative_paths=(_ARTIFACT_MANIFEST_NAME,),
            )
            return mounted_objects
    prefix = f"{base_key}/" if base_key else ""
    listed = await _list_sdk_object_metadata_with_runtime(
        bucket_name,
        prefix,
        obs_runtime=obs_runtime,
        mount_root=mount_root,
        limit=limit,
    )
    objects: list[ListedArtifactObject] = []
    for item in listed:
        safe_key = normalize_obs_object_key(item.key, bucket_name)
        relative_path = _relative_output_path(safe_key, base_key)
        if relative_path is None:
            continue
        size_bytes = item.size_bytes
        if size_bytes is None:
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
        if limit is not None and len(objects) >= limit:
            return objects
    return objects


async def _list_sdk_object_metadata_with_runtime(
    bucket_name: str,
    prefix: str,
    *,
    obs_runtime: Any,
    mount_root: str,
    limit: int | None,
) -> list[ObsListedObject]:
    """Fetch bounded LIST metadata without N sequential object HEADs."""
    objects: list[ObsListedObject] = []
    marker: str | None = None
    while True:
        remaining = None if limit is None else limit - len(objects)
        if remaining is not None and remaining <= 0:
            return objects
        max_keys = 1000 if remaining is None else min(remaining, 1000)
        page, marker = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client, marker=marker, max_keys=max_keys: (
                list_object_metadata_page(
                    bucket_name,
                    prefix,
                    marker,
                    access=ObsAccessOptions(
                        client=client,
                        mount_root=mount_root,
                    ),
                    max_keys=max_keys,
                )
            ),
        )
        objects.extend(page)
        if limit is not None and len(objects) >= limit:
            return objects[:limit]
        if marker is None:
            return objects


def _list_obsfs_objects(
    directory: Path,
    *,
    base_key: str,
    bucket_name: str,
    limit: int | None = None,
    preferred_relative_paths: tuple[str, ...] = (),
) -> list[ListedArtifactObject]:
    """Build object records from one confined obsfs directory."""
    dir_path = directory
    resolved_dir_path = dir_path.resolve()
    objects: list[ListedArtifactObject] = []
    preferred: set[str] = set()
    for relative in preferred_relative_paths:
        path = dir_path / relative
        if path.is_symlink() or not path.is_file():
            continue
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_dir_path)
        except ValueError:
            continue
        object_key = f"{base_key}/{relative}" if base_key else relative
        download_ref = obs_path_from_key(bucket_name, object_key)
        objects.append(
            ListedArtifactObject(
                relative_path=relative,
                source_path=str(path),
                size_bytes=resolved_path.stat().st_size,
                download_ref=download_ref,
            )
        )
        preferred.add(relative)
        if limit is not None and len(objects) >= limit:
            return objects
    paths: Iterator[Path]
    if limit is None:
        paths = iter(sorted(dir_path.rglob("*")))
    else:
        paths = _iter_obsfs_files_bounded(dir_path)
    bounded_walk = limit is not None
    for path in paths:
        if not bounded_walk and (path.is_symlink() or not path.is_file()):
            continue
        relative_path = path.relative_to(dir_path).as_posix()
        if relative_path in preferred:
            continue
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_dir_path)
        except ValueError:
            continue
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
        if limit is not None and len(objects) >= limit:
            break
    return objects


def _iter_obsfs_files_bounded(directory: Path) -> Iterator[Path]:
    """Walk mounted output lazily so callers can stop at their hard cap."""
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_obsfs_files_bounded(Path(entry.path))


def _list_sdk_objects(
    keys: list[str],
    *,
    base_key: str,
    bucket_name: str,
    access: ObsAccessOptions,
    limit: int | None = None,
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
        if limit is not None and len(objects) >= limit:
            return objects
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
    limit: int | None = None,
) -> list[str]:
    """Return public ``/obs/<bucket>/<key>`` paths of files under output_dir.

    Args:
        output_dir: OBS-style directory path written by the run.
        bucket_name: OBS bucket the run wrote to.
        client: Runtime-owned OBS client for the SDK fallback.
        mount_root: obsfs mount root (default ``/obs``).
        limit: Optional max file count; stops the walk once reached.

    Returns:
        Public paths of every file under ``output_dir`` (directories
        excluded), or an empty list when nothing is found.
    """
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            paths: list[str] = []
            walk = (
                dir_path.rglob("*")
                if limit is not None
                else sorted(dir_path.rglob("*"))
            )
            for path in walk:
                if not path.is_file():
                    continue
                rel = path.relative_to(dir_path).as_posix()
                paths.append(
                    obs_path_from_key(
                        bucket_name,
                        f"{object_key}/{rel}" if object_key else rel,
                    )
                )
                if limit is not None and len(paths) >= limit:
                    return paths
            return paths
    keys = _list_sdk_keys(
        bucket_name,
        object_key,
        access=ObsAccessOptions(client=client, mount_root=mount_root),
        limit=limit,
    )
    return [obs_path_from_key(bucket_name, key) for key in keys]


async def list_artifact_paths_with_runtime(
    output_dir: str,
    *,
    bucket_name: str,
    obs_runtime: Any,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
    limit: int | None = None,
) -> list[str]:
    """List output paths with a separate OBS lease for each list page."""
    object_key = normalize_obs_object_key(output_dir, bucket_name)
    if obsfs_bucket_available(bucket_name, mount_root):
        dir_path = obsfs_path_for(output_dir, bucket_name, mount_root)
        if dir_path.is_dir():
            paths: list[str] = []
            walk = (
                dir_path.rglob("*")
                if limit is not None
                else sorted(dir_path.rglob("*"))
            )
            for path in walk:
                if not path.is_file():
                    continue
                relative = path.relative_to(dir_path).as_posix()
                paths.append(
                    obs_path_from_key(
                        bucket_name,
                        f"{object_key}/{relative}" if object_key else relative,
                    )
                )
                if limit is not None and len(paths) >= limit:
                    return paths
            return paths
    keys = await _list_sdk_keys_with_runtime(
        bucket_name,
        object_key,
        obs_runtime=obs_runtime,
        mount_root=mount_root,
        limit=limit,
    )
    return [obs_path_from_key(bucket_name, key) for key in keys]


def _extend_keys_up_to_limit(
    keys: list[str],
    page: list[str],
    limit: int | None,
) -> bool:
    """Append ``page`` onto ``keys`` and return True when the cap is met."""
    if limit is None:
        keys.extend(page)
        return False
    remaining = limit - len(keys)
    if remaining <= 0:
        return True
    keys.extend(page[:remaining])
    return len(keys) >= limit


def _list_sdk_keys(
    bucket_name: str,
    prefix: str,
    *,
    access: ObsAccessOptions,
    limit: int | None = None,
) -> list[str]:
    """Fetch SDK list pages until exhausted or ``limit`` keys are collected."""
    if limit is None:
        return list_object_keys(bucket_name, prefix, access=access)
    keys: list[str] = []
    marker: str | None = None
    while True:
        page, marker = list_object_keys_page(
            bucket_name,
            prefix,
            marker,
            access=access,
        )
        if _extend_keys_up_to_limit(keys, page, limit):
            return keys
        if marker is None:
            return keys


async def _list_sdk_keys_with_runtime(
    bucket_name: str,
    prefix: str,
    *,
    obs_runtime: Any,
    mount_root: str,
    limit: int | None = None,
) -> list[str]:
    """Fetch list pages through individually scoped SDK operations."""
    keys: list[str] = []
    marker: str | None = None
    while True:
        page, marker = await obs_runtime.run(
            ObsProfileName.PRIMARY,
            lambda client, current=marker: list_object_keys_page(
                bucket_name,
                prefix,
                current,
                access=ObsAccessOptions(
                    client=client,
                    mount_root=mount_root,
                ),
            ),
        )
        if _extend_keys_up_to_limit(keys, page, limit):
            return keys
        if marker is None:
            return keys
