# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Storage helpers for Analyst-backed analysis workflows.

Exports OBS access options, metadata data-list lookup, and output directory
builders. Directory helpers prefer obsfs paths and fall back to the OBS SDK
when the mount is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, NamedTuple

from obs import ObsClient

from ...common.prompts import file_cache_fingerprint
from ...config.data_loaders import load_species_data
from ...config.defaults import AnalystConfig
from ...config.relay_mode import relay_mode_enabled
from ...config.settings import get_sensitive_config
from ...storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_path_for,
)
from ...storage.path_policy import RunIdentity, task_output_key

__all__ = [
    "ObsAccessOptions",
    "create_output_dir",
    "ensure_run_output_dir",
    "get_data_list",
]

logger = logging.getLogger(__name__)

ANALYST_CONFIG = AnalystConfig()


class ObsAccessOptions(NamedTuple):
    """Resolved OBS endpoint and credential settings.

    Attributes:
        access_key_id: OBS access key id.
        secret_access_key: OBS secret access key.
        obs_server: OBS service endpoint.
        bucket_name: OBS bucket that stores workflow data.
    """

    access_key_id: str
    secret_access_key: str
    obs_server: str
    bucket_name: str


def get_data_list(
    data_file: str, analysis_type: str, species: str
) -> Dict[str, Any]:
    """Return configured data files for one analysis type and species.

    Args:
        data_file: JSON metadata file containing analysis data lists.
        analysis_type: Analysis type key to select from the metadata.
        species: Species key to select within the analysis type.

    Returns:
        Mapping of OBS data-file paths to their natural-language
        descriptions for the requested analysis and species. For analyses
        that subdivide species into subgroups (e.g.
        ``gene_expression_analysis`` with cultivars/tissues/treatments)
        some values are themselves ``Dict[str, str]`` of file paths to
        descriptions, so the value type is widened to ``Any``.

    Raises:
        FileNotFoundError: If ``data_file`` cannot be read.
        KeyError: If the analysis type or species is missing.
    """
    try:
        cache_path, mtime_ns, size = file_cache_fingerprint(data_file)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Data file not found: {data_file}") from exc
    return _get_data_list_cached(
        cache_path,
        analysis_type,
        species,
        mtime_ns,
        size,
    )


def _get_data_list_cached(
    data_file: str,
    analysis_type: str,
    species: str,
    mtime_ns: int,
    size: int,
) -> Dict[str, Any]:
    """Select a data list from cached static species metadata."""
    del mtime_ns, size
    data = load_species_data(data_file)
    try:
        analysis_data_list = data[analysis_type]
    except KeyError as exc:
        raise KeyError(f"Analysis type not found: {analysis_type}") from exc
    try:
        data_list = analysis_data_list[species]
    except KeyError as exc:
        raise KeyError(f"Species not found: {species}") from exc
    return data_list


def create_output_dir(user_id: str, task: str, **kwargs: Any) -> str:
    """Create a unique OBS output directory for analysis tasks.

    Args:
        user_id: User id used in the generated run-scoped path.
        task: Task label used as the output directory scope.
        **kwargs: Optional OBS credentials, endpoint, bucket,
            obsfs_mount_root, and run_identity overrides.

    Returns:
        OBS path for the created output directory.

    Raises:
        OSError: If both obsfs creation and OBS SDK fallback fail.
    """
    default_access_key_id, default_secret_access_key = (
        get_sensitive_config().obs_credentials()
    )
    access_key_id = kwargs.get("access_key_id", default_access_key_id)
    secret_access_key = kwargs.get(
        "secret_access_key", default_secret_access_key
    )
    obs_server = kwargs.get("obs_server", ANALYST_CONFIG.OBS_SERVER)
    bucket_name = kwargs.get("bucket_name", ANALYST_CONFIG.BUCKET_NAME)
    obsfs_mount_root = kwargs.get(
        "obsfs_mount_root",
        DEFAULT_OBSFS_MOUNT_ROOT,
    )
    run_identity = kwargs.get("run_identity")
    if not isinstance(run_identity, RunIdentity):
        run_identity = RunIdentity.create(user_id=user_id, scope=task)
    output_dir = task_output_key(run_identity, task)
    if relay_mode_enabled():
        # OBS has a flat namespace, so the zero-byte directory marker is
        # cosmetic: the remote analysis platform creates the path when it
        # writes results there. A relay child (no operator OBS creds)
        # therefore returns the run-scoped path without minting a marker,
        # avoiding a relay round trip on every analysis submit.
        return obs_path_from_key(bucket_name, output_dir)
    try:
        _create_output_dir_obsfs(
            output_dir,
            bucket_name,
            obsfs_mount_root,
        )
        return obs_path_from_key(bucket_name, output_dir)
    except OSError:
        return _create_output_dir_sdk(
            output_dir,
            ObsAccessOptions(
                access_key_id,
                secret_access_key,
                obs_server,
                bucket_name,
            ),
        )


def ensure_run_output_dir(
    config: Any,
    sensitive_config: Any,
    task: str,
    run_identity: RunIdentity,
    output_dir: str | None = None,
) -> str:
    """Return an existing output dir or create one under a run identity.

    Args:
        config: Public config object with OBS endpoint and bucket fields.
        sensitive_config: Sensitive config object with OBS credentials.
        task: Task label used as the output directory scope.
        run_identity: Run identity used for path construction.
        output_dir: Existing output directory to reuse when provided.

    Returns:
        Existing ``output_dir`` or a newly created OBS output directory.

    Raises:
        OSError: If output directory creation fails.
    """
    if output_dir:
        return output_dir
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return create_output_dir(
        user_id=run_identity.user_id,
        task=task,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=config.OBS_SERVER,
        bucket_name=config.BUCKET_NAME,
        run_identity=run_identity,
    )


def _create_output_dir_obsfs(
    output_dir: str,
    bucket_name: str,
    obsfs_mount_root: str,
) -> None:
    """Create an output directory through obsfs."""
    _require_obsfs_bucket(bucket_name, obsfs_mount_root)
    output_path = obsfs_path_for(output_dir, bucket_name, obsfs_mount_root)
    output_path.mkdir(parents=True, exist_ok=True)


def _create_output_dir_sdk(
    output_dir: str,
    access: ObsAccessOptions,
) -> str:
    """Create an output directory through the OBS SDK fallback."""
    obs_client = ObsClient(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        server=access.obs_server,
    )
    try:
        response = obs_client.putContent(
            bucketName=access.bucket_name,
            objectKey=output_dir,
            content=None,
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"/obs/{access.bucket_name}/{output_dir}"
        raise OSError(_obs_error_message("Put File Failed", response))
    except Exception as exc:
        logger.exception("OBS upload failed for output dir %s", output_dir)
        raise OSError("upload failed") from exc


def _obs_error_message(message: str, response: Any) -> str:
    """Return a compact OBS response error message."""
    details = {
        "requestId": getattr(response, "requestId", "unknown"),
        "errorCode": getattr(response, "errorCode", "unknown"),
        "errorMessage": getattr(response, "errorMessage", "unknown"),
    }
    return "\n".join(
        [message, *[f"{key}: {value}" for key, value in details.items()]]
    )


def _require_obsfs_bucket(bucket_name: str, obsfs_mount_root: str) -> None:
    """Raise when an obsfs bucket root is not currently available."""
    if not obsfs_bucket_available(bucket_name, obsfs_mount_root):
        raise FileNotFoundError(
            f"OBSFS bucket is not available: {obsfs_mount_root}/{bucket_name}"
        )
