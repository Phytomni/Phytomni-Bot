# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS storage and static-data helpers for Analyst workflows."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from traceback import format_exc
from typing import Any, Dict, NamedTuple, Optional
from uuid import uuid1

from obs import GetObjectHeader, ObsClient, PutObjectHeader

from .config.defaults import AnalystConfig
from .config.settings import SensitiveConfig
from .utils import file_cache_fingerprint, load_json_file

ANALYST_CONFIG = AnalystConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)


class ObsAccessOptions(NamedTuple):
    """Resolved OBS endpoint and credential settings."""

    access_key_id: str
    secret_access_key: str
    obs_server: str
    bucket_name: str


@dataclass(frozen=True)
class ObsDownloadOptions:
    """Resolved options for downloading analyst results from OBS."""

    download_path: str
    access: ObsAccessOptions
    target_file_feature: tuple[str, ...]
    marker: Optional[str]
    max_keys: int
    if_download_all: bool

    @classmethod
    def from_kwargs(cls, values: Dict[str, Any]):
        """Build options from keyword-compatible overrides."""
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
            target_file_feature=tuple(target_file_feature),
            marker=values.get("marker", ANALYST_CONFIG.DOWNLOAD_MARKER),
            max_keys=values.get("max_keys", ANALYST_CONFIG.DOWNLOAD_MAX_KEYS),
            if_download_all=values.get(
                "if_download_all", ANALYST_CONFIG.IF_DOWNLOAD_ALL
            ),
        )


def upload_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = ANALYST_CONFIG.OBS_SERVER,
    bucket_name: str = ANALYST_CONFIG.BUCKET_NAME,
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
    obsclient = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        headers = PutObjectHeader()
        headers.contentType = "text/plain"
        object_file = analyst_agents_datapath.split("/")[-1]
        object_key = f"agent_data/tmp_data/{object_file}"
        response = obsclient.putFile(
            bucketName=bucket_name,
            objectKey=object_key,
            file_path=object_file,
            metadata={"meta1": "value1", "meta2": "value2"},
            headers=headers,
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"{bucket_name}:/{object_key}"
        raise OSError(
            "Put File Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put File Failed\n{format_exc()}") from exc


def delete_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = ANALYST_CONFIG.OBS_SERVER,
    bucket_name: str = ANALYST_CONFIG.BUCKET_NAME,
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
    obsclient = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        object_key = analyst_agents_datapath
        response = obsclient.deleteObject(bucket_name, object_key)
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


def get_data_list(data_file: str, analysis_type: str, species: str) -> list:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        data_file: data_list_file for json format
        analysis_type: analysis_type[evolution_analysis, deepgo2_analysis,
                                     structure_analysis, prompter_analysis,
                                     protein_design_analysis,
                                     gene_expression_analysis, ppi_analysis]
        species: 65 species ...

    Returns:
        data_list for analysis
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
) -> list:
    """Select a data list from cached static species metadata."""
    del mtime_ns, size
    data = load_json_file(data_file)
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
    """Create a unique output directory for analysis tasks in Object Storage
        Service.

    This function generates a timestamped, user-specific directory structure
    in OBS for storing analysis results. The directory path includes user ID,
    task type, timestamp, and a unique identifier to prevent conflicts.

    Args:
        user_id: Unique identifier for the user requesting the analysis.
        task: Name or type of the analysis task (e.g., 'network_task',
            'evolution_task').
        access_key_id: Access key identifier for Object Storage Service (OBS)
            authentication, required for directory creation operations.
        secret_access_key: Secret access key for OBS authentication, paired
            with access_key_id for secure storage operations.
        obs_server: Base URL endpoint for the Object Storage Service where
            the directory will be created.
        bucket_name: Name of the OBS bucket where the output directory
            will be created.

    Returns:
        The full OBS path to the created output directory in the format:
        '/obs/{bucket_name}/agent_data/user_data/'
        '{user_id}/output/{task}_{timestamp}_{uuid}/'

    Raises:
        OSError: If the directory creation fails due to OBS connectivity
            issues, authentication problems, or insufficient permissions.

    Examples:
        Basic usage:
            >>> output_path = create_output_dir(
            ...     user_id='user123',
            ...     task='gene_analysis'
            ... )
            >>> print(output_path)
            '/obs/phytomni/agent_data/user_data/'
            'user123/output/gene_analysis_1640995200_abc123/'

        Custom configuration:
            >>> output_path = create_output_dir(
            ...     user_id='researcher001',
            ...     task='network_analysis',
            ...     bucket_name='custom_bucket'
            ... )

    Note:
        The generated directory path includes a timestamp and UUID to ensure
        uniqueness across multiple analysis runs. The directory is created
        as an empty placeholder in OBS and can be used immediately for
        storing analysis results.
    """
    access_key_id = kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID)
    secret_access_key = kwargs.get(
        "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
    )
    obs_server = kwargs.get("obs_server", ANALYST_CONFIG.OBS_SERVER)
    bucket_name = kwargs.get("bucket_name", ANALYST_CONFIG.BUCKET_NAME)
    obs_client = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    try:
        output_dir = (
            f"agent_data/user_data/{user_id}/output/"
            f"{task}_{int(time.time())}_{uuid1()}/"
        )
        response = obs_client.putContent(
            bucketName=bucket_name, objectKey=output_dir, content=None
        )
        status_code = getattr(response, "status", None)
        if status_code is not None and status_code < 300:
            return f"/obs/{bucket_name}/{output_dir}"
        raise OSError(
            f"Put File Failed\n"
            f"requestId: {getattr(response, 'requestId', 'unknown')}\n"
            f"errorCode: {getattr(response, 'errorCode', 'unknown')}\n"
            f"errorMessage: {getattr(response, 'errorMessage', 'unknown')}"
        )
    except Exception as exc:
        raise OSError(f"Put File Failed\n{format_exc()}") from exc


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
    output_path = _download_output_path(task_dir, options.download_path)
    headers = GetObjectHeader()
    headers.if_modified_since = "date"
    obs_client = _obs_client(
        access_key_id=options.access.access_key_id,
        secret_access_key=options.access.secret_access_key,
        obs_server=options.access.obs_server,
    )
    try:
        for object_key in _list_obs_object_keys(
            obs_client, options, obs_output_path
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
