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

from mcp_server_phytomni.storage.obs_relay_ops import list_object_keys
from mcp_server_phytomni.storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    normalize_obs_object_key,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_path_for,
)

__all__ = ["list_artifact_paths"]


def list_artifact_paths(
    output_dir: str,
    *,
    bucket_name: str,
    obs_server: str,
    mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT,
) -> list[str]:
    """Return public ``/obs/<bucket>/<key>`` paths of files under output_dir.

    Args:
        output_dir: OBS-style directory path written by the run.
        bucket_name: OBS bucket the run wrote to.
        obs_server: OBS endpoint for the SDK fallback.
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
    keys = list_object_keys(bucket_name, object_key, obs_server=obs_server)
    return [obs_path_from_key(bucket_name, key) for key in keys]
