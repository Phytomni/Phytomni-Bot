# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Obsfs-first scratch directory resolution.

Classes: ScratchTarget.
Functions: resolve_scratch_dir.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_path_for,
)
from .path_policy import RunIdentity, task_downloads_key, task_tmp_key

ScratchKind = Literal["downloads", "tmp"]


@dataclass(frozen=True)
class ScratchTarget:
    """Storage location options used by resolve_scratch_dir.

    Attributes:
        bucket_name: OBS bucket name expected at obsfs_mount_root.
        local_fallback: Local directory used when obsfs is unavailable.
        obsfs_mount_root: Root path for obsfs mounts (default /obs).
    """

    bucket_name: str
    local_fallback: Path
    obsfs_mount_root: str | Path = DEFAULT_OBSFS_MOUNT_ROOT


def resolve_scratch_dir(
    kind: ScratchKind,
    run_identity: RunIdentity,
    task: str,
    target: ScratchTarget,
) -> str:
    """Return an obsfs scratch directory when mounted, else create local.

    Args:
        kind: Scratch kind to provision, either downloads or tmp.
        run_identity: Run identity that scopes the path under user/run.
        task: Task identifier used as the final path segment.
        target: Bucket name, local fallback, and obsfs mount root.

    Returns:
        Public OBS path under obsfs when the bucket is mounted; otherwise
        a string path to a freshly created local scratch directory.
    """
    if obsfs_bucket_available(target.bucket_name, target.obsfs_mount_root):
        try:
            return _resolve_obsfs_scratch_dir(kind, run_identity, task, target)
        except OSError:
            pass
    return _resolve_local_scratch_dir(
        run_identity, task, target.local_fallback
    )


def _resolve_obsfs_scratch_dir(
    kind: ScratchKind,
    run_identity: RunIdentity,
    task: str,
    target: ScratchTarget,
) -> str:
    """Create the obsfs scratch directory and return its public OBS path."""
    key = (
        task_downloads_key(run_identity, task)
        if kind == "downloads"
        else task_tmp_key(run_identity, task)
    )
    obsfs_path_for(key, target.bucket_name, target.obsfs_mount_root).mkdir(
        parents=True, exist_ok=True
    )
    return obs_path_from_key(target.bucket_name, key)


def _resolve_local_scratch_dir(
    run_identity: RunIdentity,
    task: str,
    local_fallback: Path,
) -> str:
    """Create a run-scoped local scratch directory and return its path."""
    local_path = local_fallback / run_identity.run_id / task
    local_path.mkdir(parents=True, exist_ok=True)
    return str(local_path)
