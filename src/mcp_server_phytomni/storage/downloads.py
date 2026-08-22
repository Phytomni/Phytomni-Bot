# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS download and upload-context conversion helpers.

Classes: ObsDownloadOptions, ObsTransferContext, ResolvedObsFile.
Functions: download_upload_context, download_obs_file,
    download_obs_list,
    convert_single_file, convert_multi_files, download_list_convert.
"""

import asyncio
import logging
from collections.abc import Mapping
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markitdown import MarkItDown

from ..common.docs import format_upload_context
from ..common.relay_client import current_relay_client
from ..config.defaults import ServerConfig
from ..config.relay_mode import relay_mode_enabled
from ..runtime.outbound import (
    ObsClientRuntime,
    ObsProfileName,
    current_outbound_runtime,
)
from .obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    normalize_obs_object_key,
    obsfs_path_for,
)
from .path_policy import RunIdentity

logger = logging.getLogger(__name__)

SERVER_CONFIG = ServerConfig()

__all__ = [
    "ConvertedDocumentText",
    "ObsDownloadOptions",
    "ObsTransferContext",
    "ResolvedObsFile",
    "convert_document_file",
    "convert_multi_files",
    "convert_single_file",
    "download_list_convert",
    "download_obs_file",
    "download_obs_list",
    "download_obs_source",
    "download_upload_context",
]


@dataclass(frozen=True)
class ObsDownloadOptions:
    """OBS endpoint and retry options for file downloads.

    Attributes:
        bucket_name: Default bucket used for object-key normalization.
        obsfs_mount_root: Local obsfs mount root used before SDK fallback.
        part_size: Multipart download part size.
        task_num: Multipart download worker count for the OBS SDK.
        max_retries: Maximum retry attempts for SDK downloads.
    """

    bucket_name: str = SERVER_CONFIG.BUCKET_NAME
    obsfs_mount_root: str = DEFAULT_OBSFS_MOUNT_ROOT
    part_size: int = SERVER_CONFIG.PART_SIZE
    task_num: int = SERVER_CONFIG.TASK_NUM
    max_retries: int = SERVER_CONFIG.MAX_RETRIES


@dataclass(frozen=True)
class ObsTransferContext:
    """Resolved OBS transfer settings used across download helpers.

    Attributes:
        server_dir: Local temporary directory for SDK downloads.
        download: OBS bucket, mount, and retry options.
        max_concurrency: Maximum concurrent OBS downloads.
        max_workers: Maximum process workers for document conversion.
    """

    server_dir: str
    download: ObsDownloadOptions
    max_concurrency: int = SERVER_CONFIG.MAX_CONCURRENCY
    max_workers: int = SERVER_CONFIG.MAX_WORKERS


@dataclass(frozen=True)
class ResolvedObsFile:
    """Local file path plus cleanup behavior for one OBS source file.

    Attributes:
        file_path: Local file path resolved through obsfs or SDK download.
        cleanup: Whether conversion should delete the local file afterward.
    """

    file_path: str
    cleanup: bool


async def download_upload_context(
    obs_file_list: list[str],
    config: Any,
) -> tuple[str, int]:
    """Download OBS uploads and format them as bounded prompt context.

    Args:
        obs_file_list: OBS paths for uploaded user context files.
        config: Runtime config with OBS, temporary path, and token limits.
    Returns:
        Formatted upload context and the resulting character length.
    """
    if not obs_file_list:
        return "", 0
    upload_texts = await download_list_convert(
        obs_file_list=obs_file_list,
        server_dir=config.TEMP_DIR,
        bucket_name=config.BUCKET_NAME,
        part_size=config.PART_SIZE,
        task_num=config.TASK_NUM,
        max_retries=config.MAX_RETRIES,
        max_concurrency=config.MAX_CONCURRENCY,
        max_workers=config.MAX_WORKERS,
    )
    return format_upload_context(upload_texts, max_tokens=config.MAX_TOKENS)


async def download_obs_source(
    obs_file: str,
    server_dir: str,
    **kwargs: Any,
) -> ResolvedObsFile:
    """Resolve one OBS object to a local file without loading it into RAM."""
    context = _obs_transfer_context(server_dir, kwargs)
    return await _resolve_obs_file(obs_file, context)


async def download_obs_file(
    obs_file: str,
    server_dir: str,
    **kwargs: Any,
) -> str:
    """Download a single file from Object Storage Service (OBS).

    This function downloads a file from a specified OBS bucket to a local
    directory. It includes a retry mechanism with exponential backoff for
    transient errors.

    Args:
        obs_file: The object key (path) of the file in the OBS bucket.
        server_dir: The local directory where the file will be downloaded.
        **kwargs: Keyword-compatible OBS transfer overrides.

    Returns:
        The local path to the downloaded file.

    Raises:
        OSError: If the file download fails after all retry attempts.
    """
    resolved_file = await download_obs_source(obs_file, server_dir, **kwargs)
    return resolved_file.file_path


def _obs_transfer_context(
    server_dir: str,
    values: Mapping[str, Any],
) -> ObsTransferContext:
    """Build an OBS transfer context from keyword-compatible overrides."""
    transfer_context = values.get("transfer_context")
    if transfer_context is not None:
        return transfer_context
    return ObsTransferContext(
        server_dir=server_dir,
        download=ObsDownloadOptions(
            bucket_name=values.get("bucket_name", SERVER_CONFIG.BUCKET_NAME),
            obsfs_mount_root=values.get(
                "obsfs_mount_root",
                DEFAULT_OBSFS_MOUNT_ROOT,
            ),
            part_size=values.get("part_size", SERVER_CONFIG.PART_SIZE),
            task_num=values.get("task_num", SERVER_CONFIG.TASK_NUM),
            max_retries=values.get("max_retries", SERVER_CONFIG.MAX_RETRIES),
        ),
        max_concurrency=values.get(
            "max_concurrency",
            SERVER_CONFIG.MAX_CONCURRENCY,
        ),
        max_workers=values.get("max_workers", SERVER_CONFIG.MAX_WORKERS),
    )


def _obs_object_key(obs_file: str, bucket_name: str) -> str:
    """Normalize accepted OBS path forms to an object key."""
    return normalize_obs_object_key(obs_file, bucket_name)


async def _resolve_obs_file(
    obs_file: str,
    context: ObsTransferContext,
) -> ResolvedObsFile:
    """Return an obsfs source file or a downloaded temporary file."""
    if relay_mode_enabled():
        return ResolvedObsFile(
            file_path=await _download_obs_file_via_relay(obs_file, context),
            cleanup=True,
        )
    obsfs_file = _obsfs_source_file(obs_file, context)
    if obsfs_file is not None:
        return ResolvedObsFile(file_path=str(obsfs_file), cleanup=False)
    return ResolvedObsFile(
        file_path=await _download_obs_file_from_sdk(obs_file, context),
        cleanup=True,
    )


def _obsfs_source_file(
    obs_file: str,
    context: ObsTransferContext,
) -> Path | None:
    """Return a readable obsfs path for a source file when available."""
    try:
        source_path = obsfs_path_for(
            obs_file,
            context.download.bucket_name,
            context.download.obsfs_mount_root,
        )
        if source_path.is_file():
            return source_path
    except OSError:
        return None
    return None


def _local_download_target(obs_file: str, context: ObsTransferContext) -> str:
    """Return a fresh run-scoped local path for one OBS download."""
    user_name = _temp_download_group(obs_file)
    run_identity = RunIdentity.create(user_id=user_name, scope="obs-download")
    server_path = (
        Path(context.server_dir)
        / run_identity.user_id
        / run_identity.date_stamp
        / run_identity.run_id
    )
    server_path.mkdir(parents=True, exist_ok=True)
    return str(server_path / Path(obs_file).name)


async def _download_obs_file_via_relay(
    obs_file: str,
    context: ObsTransferContext,
) -> str:
    """Stream one OBS object through the relay to a local temp file."""
    server_file = _local_download_target(obs_file, context)
    await current_relay_client().get_obs_object_to_path(
        obs_file,
        Path(server_file),
        message="Failed to download file via relay",
    )
    return server_file


async def _download_obs_file_from_sdk(
    obs_file: str,
    context: ObsTransferContext,
) -> str:
    """Download one OBS object to the temporary directory using the SDK."""
    server_file = _local_download_target(obs_file, context)
    obs_runtime = current_outbound_runtime().obs
    if obs_runtime is None:
        raise OSError("OBS runtime is unavailable")
    object_key = _obs_object_key(obs_file, context.download.bucket_name)
    return await _download_obs_file_with_retry(
        obs_runtime,
        object_key,
        server_file,
        context,
    )


def _temp_download_group(obs_file: str) -> str:
    """Return a stable temporary subdirectory name for an OBS source path."""
    object_key = str(obs_file).rstrip("/")
    parts = [part for part in object_key.split("/") if part]
    if len(parts) >= 2:
        return parts[-2]
    return "uploads"


async def _download_obs_file_with_retry(
    obs_runtime: ObsClientRuntime,
    object_key: str,
    server_file: str,
    context: ObsTransferContext,
) -> str:
    """Download one OBS object with retry and backoff."""
    for attempt in range(context.download.max_retries + 1):
        try:
            download_response = await _download_obs_file_once(
                obs_runtime,
                object_key,
                server_file,
                context,
            )
            if download_response.status < 300:
                return server_file
            raise _obs_download_error(download_response)
        except Exception as exc:
            if attempt < context.download.max_retries:
                await asyncio.sleep(1.5**attempt)
                continue
            logger.exception("OBS download failed for object %s", object_key)
            raise OSError("download failed") from exc
    return server_file


async def _download_obs_file_once(
    obs_runtime: ObsClientRuntime,
    object_key: str,
    server_file: str,
    context: ObsTransferContext,
):
    """Run one blocking OBS download through the owned runtime."""
    return await obs_runtime.run(
        ObsProfileName.PRIMARY,
        lambda obs_client: obs_client.downloadFile(
            bucketName=context.download.bucket_name,
            objectKey=object_key,
            downloadFile=server_file,
            partSize=context.download.part_size,
            taskNum=context.download.task_num,
            enableCheckpoint=True,
        ),
    )


def _obs_download_error(download_response: Any) -> OSError:
    """Build an OSError from an OBS download response."""
    return OSError(
        "Download File Failed\n"
        f"requestId: {download_response.requestId}\n"
        f"errorCode: {download_response.errorCode}\n"
        f"errorMessage: {download_response.errorMessage}"
    )


async def download_obs_list(
    obs_file_list: list[str],
    server_dir: str,
    **kwargs: Any,
) -> list[str]:
    """Download multiple files from OBS concurrently.

    Each OBS network attempt is bounded by the process-wide outbound OBS pool.

    Args:
        obs_file_list: A list of object keys (paths) for the files to be
            downloaded from OBS.
        server_dir: The local directory where the files will be downloaded.
        **kwargs: Keyword-compatible OBS transfer overrides.

    Returns:
        A list of local paths to the downloaded files.
    """
    context = _obs_transfer_context(server_dir, kwargs)
    return await asyncio.gather(
        *(
            download_obs_file(
                obs_file=obs_file,
                server_dir=context.server_dir,
                transfer_context=context,
            )
            for obs_file in obs_file_list
        )
    )


def convert_single_file(server_file: str, cleanup: bool = True) -> str:
    """Convert a single file to Markdown format.

    This function uses the `MarkItDown` library to convert a file (e.g., PDF,
    DOCX) into Markdown text. Temporary SDK downloads are deleted after
    conversion, while obsfs source files can be preserved.

    Args:
        server_file: The local path to the file to be converted.
        cleanup: Whether to delete the file after conversion.

    Returns:
        A string containing the Markdown content of the converted file.
    """
    md_instance = MarkItDown(
        docintel_endpoint="<document_intelligence_endpoint>"
    )
    result = md_instance.convert(server_file)
    server_path = Path(server_file)
    if cleanup:
        server_path.unlink()
    return result.text_content


@dataclass(frozen=True, slots=True)
class ConvertedDocumentText:
    """Markdown plus ordered form-feed sections for one converted file."""

    markdown: str
    sections: tuple[str, ...]


def convert_document_file(
    path: str | Path, *, cleanup: bool = False
) -> ConvertedDocumentText:
    """Convert one local document through MarkItDown and split on form feed."""
    markdown = convert_single_file(str(path), cleanup=cleanup)
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("document conversion returned no text")
    sections = tuple(
        part.strip() for part in markdown.split("\f") if part.strip()
    )
    if not sections:
        raise ValueError("document conversion returned no text")
    return ConvertedDocumentText(markdown=markdown, sections=sections)


def convert_multi_files(
    server_file_list: list[str],
    max_workers: int = SERVER_CONFIG.MAX_WORKERS,
) -> list[str]:
    """Convert multiple files to Markdown in parallel.

    This function uses a `ProcessPoolExecutor` to convert a list of files to
    Markdown format concurrently, leveraging multiple CPU cores.

    Args:
        server_file_list: A list of local file paths to be converted.
        max_workers: The maximum number of worker processes to use for the
            conversion.

    Returns:
        A list of strings, where each string is the Markdown content of a
        converted file.
    """
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(convert_single_file, server_file_list))
    return results


async def download_list_convert(
    obs_file_list: list[str],
    server_dir: str,
    executor: Executor | None = None,
    **kwargs: Any,
) -> list[str]:
    """Download, and convert multiple files from OBS in a parallel pipeline.

    This function orchestrates a workflow where files are downloaded from OBS
    concurrently and then converted to Markdown in a parallel process pool.
    It is designed for efficient batch processing of documents.

    Args:
        obs_file_list: A list of object keys for the files in OBS.
        server_dir: The local directory for temporary file storage.
        executor: An optional existing `Executor` to reuse for conversions.
            If None, a new `ProcessPoolExecutor` is created and managed.
        **kwargs: Keyword-compatible OBS transfer overrides.

    Returns:
        A list of strings, each containing the Markdown content of a
        processed file.
    """
    context = _obs_transfer_context(server_dir, kwargs)
    if context.max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    should_shutdown = executor is None
    active_executor = executor or ProcessPoolExecutor(
        max_workers=context.max_workers
    )

    try:
        return [
            await _download_and_convert(obs_file, context, active_executor)
            for obs_file in obs_file_list
        ]
    finally:
        if should_shutdown:
            active_executor.shutdown(wait=True)


async def _download_and_convert(
    obs_file: str,
    context: ObsTransferContext,
    executor: Executor,
) -> str:
    """Download one OBS file and convert it to Markdown."""
    source_file = await _resolve_obs_file(obs_file, context)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        convert_single_file,
        source_file.file_path,
        source_file.cleanup,
    )
