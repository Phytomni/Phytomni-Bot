# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility exports for OBS path helpers."""

from .storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    OBSFS_FALLBACK_ERRORS,
    ObsPathError,
    bucket_colon_path,
    normalize_obs_object_key,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_bucket_root,
    obsfs_or_sdk,
    obsfs_path_exists,
    obsfs_path_for,
)

__all__ = [
    "DEFAULT_OBSFS_MOUNT_ROOT",
    "OBSFS_FALLBACK_ERRORS",
    "ObsPathError",
    "bucket_colon_path",
    "normalize_obs_object_key",
    "obs_path_from_key",
    "obsfs_bucket_available",
    "obsfs_bucket_root",
    "obsfs_or_sdk",
    "obsfs_path_exists",
    "obsfs_path_for",
]
