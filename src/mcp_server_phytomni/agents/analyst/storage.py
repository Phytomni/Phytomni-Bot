# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS upload, delete, and result-download helpers for Analyst workflows.

Exports download options plus public helpers that upload local files or
generated metadata, delete analyst OBS objects, and download result files.
Helpers prefer obsfs operations and fall back to the OBS SDK.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from traceback import format_exc
from typing import Any, Dict, Mapping, Optional

from obs import GetObjectHeader, ObsClient, PutObjectHeader

from ...config.defaults import AnalystConfig
from ...config.settings import SensitiveConfig
from ...storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    bucket_colon_path,
    normalize_obs_object_key,
    obsfs_bucket_available,
    obsfs_path_for,
)
from ..shared.analysis_storage import ObsAccessOptions

ANALYST_CONFIG = AnalystConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)


@dataclass(frozen=True)
class ObsDownloadOptions:
    """Resolved options for downloading analyst results from OBS.

    Attributes:
        download_path: Local root used for downloaded result files.
        access: OBS endpoint and credential settings.
        obsfs_mount_root: Local root where obsfs buckets are mounted.
        target_file_feature: File suffixes selected when not downloading all.
        marker: Optional OBS pagination marker.
        max_keys: Maximum OBS objects listed per request.
        if_download_all: Whether to download every listed object.
    """

    download_path: str
    access: ObsAccessOptions
    obsfs_mount_root: str
    target_file_feature: tuple[str, ...]
    marker: Optional[str]
    max_keys: int
    if_download_all: bool

    @classmethod
    def from_kwargs(cls, values: Dict[str, Any]):
        """Build options from keyword-compatible overrides.

        Args:
            values: Wrapper keyword overrides and default-compatible fields.

        Returns:
            Resolved OBS download options.
        """
        target_file_feature = values.get("target_file_feature")
        if target_file_feature is None:
            target_file_feature = ANALYST_CONFIG.TARGET_FILE_FEATURE
        return cls(
            download_path=values.get(
                "download_path", ANALYST_CONFIG.DOWNLOAD_PATH
            ),
            access=ObsAccessOptions(
                access_key_id=values.get(
                    "access_key_id", DEFAULT_ACCESS_KEY_ID
                ),
                secret_access_key=values.get(
                    "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
                ),
                obs_server=values.get("obs_server", ANALYST_CONFIG.OBS_SERVER),
                bucket_name=values.get(
                    "bucket_name", ANALYST_CONFIG.BUCKET_NAME
                ),
            ),
            obsfs_mount_root=values.get(
                "obsfs_mount_root",
                DEFAULT_OBSFS_MOUNT_ROOT,
            ),
            target_file_feature=tuple(target_file_feature),
            marker=values.get("marker", ANALYST_CONFIG.DOWNLOAD_MARKER),
            max_keys=values.get("max_keys", ANALYST_CONFIG.DOWNLOAD_MAX_KEYS),
            if_download_all=values.get(
                "if_download_all", ANALYST_CONFIG.IF_DOWNLOAD_ALL
            ),
        )


def _obs_access_from_values(values: Mapping[str, Any]) -> ObsAccessOptions:
    """Build OBS access options from keyword-compatible values."""
    return ObsAccessOptions(
        access_key_id=values.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        secret_access_key=values.get(
            "secret_access_key",
            DEFAULT_SECRET_ACCESS_KEY,
        ),
        obs_server=values.get("obs_server", ANALYST_CONFIG.OBS_SERVER),
        bucket_name=values.get("bucket_name", ANALYST_CONFIG.BUCKET_NAME),
    )


def upload_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = ANALYST_CONFIG.OBS_SERVER,
    bucket_name: str = ANALYST_CONFIG.BUCKET_NAME,
    **kwargs: Any,
) -> str:
    """
    Uploads data to an Object Storage Service (OBS) bucket.

    This function takes a local file path and uploads the file to a specified
    OBS bucket. It handles the connection and authentication with the OBS
    service.

    Args:
        analyst_agents_datapath: The local path to the file to be uploaded.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        The OBS path of the uploaded file, in the format
        'bucket_name:/object_key'.

    Raises:
        OSError: If the file upload to OBS fails.
    """
    access = ObsAccessOptions(
        access_key_id,
        secret_access_key,
        obs_server,
        bucket_name,
    )
    obsfs_mount_root = kwargs.get("obsfs_mount_root", DEFAULT_OBSFS_MOUNT_ROOT)
    object_file = Path(analyst_agents_datapath).name
    object_key = f"agent_data/tmp_data/{object_file}"
    try:
        return _upload_file_obsfs(
            analyst_agents_datapath,
            object_key,
            bucket_name,
            obsfs_mount_root,
        )
    except OSError:
        return _upload_file_sdk(
            analyst_agents_datapath,
            object_key,
            access,
        )


def upload_analyst_agents_content(
    content: str,
    object_name: str,
    **kwargs: Any,
) -> str:
    """Upload generated analyst metadata content to OBS storage.

    Args:
        content: Text content to write to OBS.
        object_name: Default object filename used when object_key is absent.
        **kwargs: Optional OBS credentials, endpoint, bucket,
            obsfs_mount_root, and object_key overrides.

    Returns:
        OBS path for the uploaded content.

    Raises:
        OSError: If both obsfs upload and OBS SDK fallback fail.
    """
    access = _obs_access_from_values(kwargs)
    obsfs_mount_root = kwargs.get("obsfs_mount_root", DEFAULT_OBSFS_MOUNT_ROOT)
    object_key = kwargs.get("object_key")
    if object_key is None:
        object_key = f"agent_data/tmp_data/{object_name}"
    else:
        object_key = normalize_obs_object_key(
            str(object_key), access.bucket_name
        )
    try:
        return _upload_content_obsfs(
            content,
            object_key,
            access.bucket_name,
            obsfs_mount_root,
        )
    except OSError:
        return _upload_content_sdk(
            content,
            object_key,
            access,
        )


def _upload_file_obsfs(
    file_path: str,
    object_key: str,
    bucket_name: str,
    obsfs_mount_root: str,
) -> str:
    """Upload one local file through obsfs and return its OBS path."""
    _require_obsfs_bucket(bucket_name, obsfs_mount_root)
    destination = obsfs_path_for(object_key, bucket_name, obsfs_mount_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, destination)
    return bucket_colon_path(bucket_name, object_key)


def _upload_content_obsfs(
    content: str,
    object_key: str,
    bucket_name: str,
    obsfs_mount_root: str,
) -> str:
    """Upload generated text content through obsfs."""
    _require_obsfs_bucket(bucket_name, obsfs_mount_root)
    destination = obsfs_path_for(object_key, bucket_name, obsfs_mount_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return bucket_colon_path(bucket_name, object_key)


def _upload_file_sdk(
    file_path: str,
    object_key: str,
    access: ObsAccessOptions,
) -> str:
    """Upload one local file through the OBS SDK fallback."""
    obsclient = ObsClient(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        server=access.obs_server,
    )
    try:
        headers = PutObjectHeader()
        headers.contentType = "text/plain"
        response = obsclient.putFile(
            bucketName=access.bucket_name,
            objectKey=object_key,
            file_path=file_path,
            metadata={"meta1": "value1", "meta2": "value2"},
            headers=headers,
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"{access.bucket_name}:/{object_key}"
        raise OSError(
            "Put File Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put File Failed\n{format_exc()}") from exc


def _upload_content_sdk(
    content: str,
    object_key: str,
    access: ObsAccessOptions,
) -> str:
    """Upload generated content through the OBS SDK fallback."""
    obsclient = ObsClient(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        server=access.obs_server,
    )
    try:
        response = obsclient.putContent(
            bucketName=access.bucket_name,
            objectKey=object_key,
            content=content,
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"{access.bucket_name}:/{object_key}"
        raise OSError(
            "Put Content Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put Content Failed\n{format_exc()}") from exc


def delete_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = ANALYST_CONFIG.OBS_SERVER,
    bucket_name: str = ANALYST_CONFIG.BUCKET_NAME,
    **kwargs: Any,
) -> str:
    """
    Deletes data from an Object Storage Service (OBS) bucket.

    This function removes a specified object from an OBS bucket using its path.
    It handles the connection and authentication required for the deletion.

    Args:
        analyst_agents_datapath: The OBS path of the file to be deleted.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        A confirmation message indicating the successful deletion of the
        object, including details like the request ID.

    Raises:
        OSError: If the file deletion from OBS fails.
    """
    access = ObsAccessOptions(
        access_key_id,
        secret_access_key,
        obs_server,
        bucket_name,
    )
    obsfs_mount_root = kwargs.get("obsfs_mount_root", DEFAULT_OBSFS_MOUNT_ROOT)
    try:
        return _delete_analyst_data_obsfs(
            analyst_agents_datapath,
            bucket_name,
            obsfs_mount_root,
        )
    except OSError:
        return _delete_analyst_data_sdk(
            analyst_agents_datapath,
            access,
        )


def _delete_analyst_data_obsfs(
    analyst_agents_datapath: str,
    bucket_name: str,
    obsfs_mount_root: str,
) -> str:
    """Delete one analyst storage path through obsfs."""
    _require_obsfs_bucket(bucket_name, obsfs_mount_root)
    object_key = normalize_obs_object_key(analyst_agents_datapath, bucket_name)
    target_path = obsfs_path_for(object_key, bucket_name, obsfs_mount_root)
    if target_path.is_dir():
        target_path.rmdir()
    else:
        target_path.unlink()
    return f"Delete Object Succeeded\nobjectKey: {object_key}"


def _delete_analyst_data_sdk(
    analyst_agents_datapath: str,
    access: ObsAccessOptions,
) -> str:
    """Delete one analyst storage path through the OBS SDK fallback."""
    obsclient = ObsClient(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        server=access.obs_server,
    )
    try:
        object_key = normalize_obs_object_key(
            analyst_agents_datapath,
            access.bucket_name,
        )
        response = obsclient.deleteObject(access.bucket_name, object_key)
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            delete_marker = getattr(response, "body", {}).get(
                "deleteMarker", "unknown"
            )
            version_id = getattr(response, "body", {}).get(
                "versionId", "unknown"
            )
            return (
                "Delete Object Succeeded\n"
                f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
                f"deleteMarker: {delete_marker}\nversionId: {version_id}"
            )
        raise OSError(
            "Delete Object Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Delete Object Failed\n{format_exc()}") from exc


def _download_output_path(task_dir: str, download_path: str) -> Path:
    """Create and return the local output path for OBS downloads."""
    output_path = Path(f"{download_path}/{task_dir}")
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def _obs_client(
    access_key_id: str,
    secret_access_key: str,
    obs_server: str,
) -> ObsClient:
    """Create an OBS client from resolved credentials."""
    return ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )


def _list_obs_object_keys(
    obs_client: ObsClient,
    options: ObsDownloadOptions,
    obs_output_path: str,
):
    """Yield downloadable object keys from a paginated OBS listing."""
    marker = options.marker
    while True:
        file_response = obs_client.listObjects(
            bucketName=options.access.bucket_name,
            prefix=obs_output_path,
            marker=marker,
            max_keys=options.max_keys,
            encoding_type="url",
        )
        file_body = _valid_obs_file_body(file_response)
        if file_body and hasattr(file_body, "contents"):
            for content in file_body.contents:
                object_key = content.key
                if not object_key.endswith("/"):
                    yield object_key
        if not _is_truncated_listing(file_body):
            break
        marker = getattr(file_body, "next_marker", None)


def _valid_obs_file_body(file_response: Any):
    """Return a successful OBS list body or raise an OSError."""
    file_status = getattr(file_response, "status", None)
    if file_status is not None and file_status < 300:
        return getattr(file_response, "body", None)
    request_id = getattr(file_response, "requestId", "unknown")
    error_code = getattr(file_response, "errorCode", "unknown")
    error_message = getattr(file_response, "errorMessage", "unknown")
    raise OSError(
        "Get File List Failed\n"
        f"requestId: {request_id}\n"
        f"errorCode: {error_code}\n"
        f"errorMessage: {error_message}"
    )


def _is_truncated_listing(file_body: Any) -> bool:
    """Return whether the OBS list response has another page."""
    return (
        file_body
        and hasattr(file_body, "is_truncated")
        and file_body.is_truncated is True
    )


def _should_download_object(
    output_file: str,
    options: ObsDownloadOptions,
) -> bool:
    """Return whether one listed OBS object should be downloaded."""
    return options.if_download_all or any(
        output_file.endswith(suffix) for suffix in options.target_file_feature
    )


def _download_obs_object(
    obs_client: ObsClient,
    headers: GetObjectHeader,
    options: ObsDownloadOptions,
    object_key: str,
    output_path: Path,
) -> str:
    """Download one OBS object and return a status message."""
    output_file = object_key.split("/")[-1]
    download_response = obs_client.getObject(
        bucketName=options.access.bucket_name,
        objectKey=object_key,
        downloadPath=str(output_path / output_file),
        headers=headers,
    )
    download_status = getattr(download_response, "status", None)
    if download_status is not None and download_status > 300:
        return f"{output_file} download failed."
    return f"{output_file} download succeed."


def download_obs_out(task_dir: str, obs_output_path: str, **kwargs: Any):
    """Download analysis results from Object Storage Service to local
        filesystem.

    This generator function downloads files from an OBS path to a local
    directory, with options for selective downloading based on file extensions
    or patterns. It supports pagination for large directories and provides
    progress feedback through yielded status messages.

    Args:
        task_dir: Local directory name where files will be downloaded, created
            under the download_path.
        obs_output_path: Source path in OBS containing the files to download
            (without bucket name prefix).
        download_path: Local filesystem path where the task directory will
            be created for storing downloaded files.
        access_key_id: Access key identifier for Object Storage Service (OBS)
            authentication, required for file download operations.
        secret_access_key: Secret access key for OBS authentication, paired
            with access_key_id for secure storage operations.
        obs_server: Base URL endpoint for the Object Storage Service where
            files are stored.
        target_file_feature: List of file extensions or suffixes to download
            (e.g., ['.png', '.pdf', '.csv']). Used when if_download_all is
            False.
        bucket_name: Name of the OBS bucket containing the source files.
        marker: Optional marker for pagination, specifying where to start
            listing objects in large directories.
        max_keys: Maximum number of objects to list per request, used for
            pagination control.
        if_download_all: Flag indicating whether to download all files (True)
            or only files matching target_file_feature patterns (False).

    Yields:
        str: Status messages for each file download attempt, indicating success
        or failure for individual files (e.g., "file.png download succeed").

    Raises:
        OSError: If OBS listing operations fail, directory creation fails,
            or file download operations encounter errors.

    Examples:
        Download specific file types:
            >>> for status in download_obs_out(
            ...     task_dir='analysis_001',
            ...     obs_output_path='results/gene_analysis/',
            ...     target_file_feature=['.png', '.csv'],
            ...     if_download_all=False
            ... ):
            ...     print(status)

        Download all files:
            >>> for status in download_obs_out(
            ...     task_dir='complete_results',
            ...     obs_output_path='analysis/output/',
            ...     if_download_all=True
            ... ):
            ...     print(status)

    Note:
        This function creates the local directory structure automatically.
        Downloads are performed with conditional headers to avoid unnecessary
        transfers. Large directories are handled through pagination to manage
        memory usage efficiently.
    """
    options = ObsDownloadOptions.from_kwargs(kwargs)
    try:
        obsfs_statuses = list(
            _download_obs_out_obsfs(task_dir, obs_output_path, options)
        )
        yield from obsfs_statuses
    except OSError:
        yield from _download_obs_out_sdk(task_dir, obs_output_path, options)


def _download_obs_out_sdk(
    task_dir: str,
    obs_output_path: str,
    options: ObsDownloadOptions,
):
    """Download analysis results through the OBS SDK fallback."""
    output_path = _download_output_path(task_dir, options.download_path)
    headers = GetObjectHeader()
    headers.if_modified_since = "date"
    obs_client = _obs_client(
        access_key_id=options.access.access_key_id,
        secret_access_key=options.access.secret_access_key,
        obs_server=options.access.obs_server,
    )
    try:
        object_prefix = normalize_obs_object_key(
            obs_output_path,
            options.access.bucket_name,
        )
        for object_key in _list_obs_object_keys(
            obs_client,
            options,
            object_prefix,
        ):
            output_file = object_key.split("/")[-1]
            if not _should_download_object(output_file, options):
                continue
            yield _download_obs_object(
                obs_client,
                headers,
                options,
                object_key,
                output_path,
            )
    except Exception as exc:
        raise OSError(f"Download File Failed\n{format_exc()}") from exc


def _download_obs_out_obsfs(
    task_dir: str,
    obs_output_path: str,
    options: ObsDownloadOptions,
):
    """Download analysis results by copying from obsfs."""
    _require_obsfs_bucket(
        options.access.bucket_name,
        options.obsfs_mount_root,
    )
    source_path = obsfs_path_for(
        obs_output_path,
        options.access.bucket_name,
        options.obsfs_mount_root,
    )
    if not source_path.is_dir():
        raise FileNotFoundError(f"OBSFS output not found: {source_path}")
    output_path = _download_output_path(task_dir, options.download_path)
    for source_file in sorted(source_path.rglob("*")):
        if not source_file.is_file():
            continue
        output_file = source_file.name
        if not _should_download_object(output_file, options):
            continue
        shutil.copy2(source_file, output_path / output_file)
        yield f"{output_file} download succeed."


def _require_obsfs_bucket(bucket_name: str, obsfs_mount_root: str) -> None:
    """Raise when an obsfs bucket root is not currently available."""
    if not obsfs_bucket_available(bucket_name, obsfs_mount_root):
        raise FileNotFoundError(
            f"OBSFS bucket is not available: {obsfs_mount_root}/{bucket_name}"
        )
