# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helpers for tokens, prompts, OBS downloads, and cached file reads."""

import asyncio
import json
from concurrent.futures import ProcessPoolExecutor
from math import ceil
from pathlib import Path
from re import sub
from traceback import format_exc
from typing import Any, List, Mapping, Optional
from uuid import uuid1
from warnings import warn

from httpx import AsyncClient, HTTPError, Timeout
from markitdown import MarkItDown
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import ObsClient
from yaml import safe_load

from .config.defaults import ServerConfig
from .config.settings import SensitiveConfig
from .func_cache import func_cache

SERVER_CONFIG = ServerConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)

FILE_CACHE_TTL = 3600


async def get_token(
    timeout: float = SERVER_CONFIG.TIMEOUT, region: str = SERVER_CONFIG.REGION
) -> str:
    """Obtain an X-Subject-Token for API authentication.

    This function authenticates with the IAM service using credentials from
    the application's settings and retrieves a temporary token for authorizing
    subsequent API requests.

    Args:
        timeout: The total request timeout in seconds. This controls both the
            connection and response phases.
        region: The geographical region for the authentication scope.

    Returns:
        A string containing the X-Subject-Token for use in authorization
        headers. The token's validity period is determined by the IAM service.

    Raises:
        McpError: If the token request fails due to network issues, invalid
            credentials, or IAM service unavailability.
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        password = SENSITIVE_CONFIG.USER_PASSWORD.get_secret_value()
        data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": SENSITIVE_CONFIG.USER_NAME,
                            "password": password,
                            "domain": {"name": SENSITIVE_CONFIG.DOMAIN_NAME},
                        },
                    },
                },
                "scope": {"project": {"name": region}},
            },
        }
        try:
            response = await client.post(
                SERVER_CONFIG.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json=data,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.headers["X-Subject-Token"]
        except HTTPError as e:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to get token: {str(e)}",
                )
            ) from e


def load_template(
    template_file: str,
    template_str: Optional[str] = None,
) -> str:
    """Load a template string from a YAML file.

    This function reads a YAML file and extracts a specific template string.
    It supports navigating nested structures within the YAML file using a
    slash-separated path.

    Args:
        template_file: The path to the YAML template file. Must be a valid
            file path with read permissions.
        template_str: A nested path (e.g., "prompts/analysis") to locate the
            template within the YAML file. Required for hierarchical files.

    Returns:
        The final template string from the specified location in the YAML.

    Raises:
        FileNotFoundError: If the `template_file` path is invalid.
        KeyError: If the `template_str` path does not exist in the YAML.
        ValueError: If the YAML is multi-level and `template_str` is not
            provided.
    """
    template_path = Path(template_file)
    if not template_path.is_file():
        raise FileNotFoundError(f"Template file not found: {template_file}")
    file_path, mtime_ns, size = file_cache_fingerprint(template_file)
    return _load_template_cached(
        file_path,
        template_str,
        mtime_ns,
        size,
    )


def file_cache_fingerprint(file_path: str) -> tuple[str, int, int]:
    """Return a stable file cache fingerprint for read-only local files."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@func_cache(
    key_params=["template_file", "template_str", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_template_cached(
    template_file: str,
    template_str: Optional[str],
    mtime_ns: int,
    size: int,
) -> str:
    """Load a template string from disk using a file-aware cache key."""
    del mtime_ns, size
    with open(template_file, "r", encoding="utf-8") as f:
        data = safe_load(f)
    if template_str:
        current = data
        for part in template_str.split("/"):
            if part not in current:
                raise KeyError(
                    f"Path '{part}' not found in template structure"
                )
            current = current[part]
        return current
    else:
        if isinstance(data, dict) and len(data) == 1:
            return next(iter(data.values()))
        else:
            raise ValueError(
                "Must specify template_str for multi-level templates"
            )


def load_json_file(file_path: str) -> Any:
    """Load a JSON file through the shared file-aware cache."""
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_json_file_cached(cache_path, mtime_ns, size)


@func_cache(
    key_params=["file_path", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_json_file_cached(file_path: str, mtime_ns: int, size: int) -> Any:
    """Load JSON from disk using a file-aware cache key."""
    del mtime_ns, size
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text_file(file_path: str) -> str:
    """Load a text file through the shared file-aware cache."""
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_text_file_cached(cache_path, mtime_ns, size)


@func_cache(
    key_params=["file_path", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_text_file_cached(file_path: str, mtime_ns: int, size: int) -> str:
    """Load text from disk using a file-aware cache key."""
    del mtime_ns, size
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(
    template: str, parameters: Optional[Mapping[str, Any]] = None
) -> str:
    """Replace placeholders in a template string with provided values.

    This function finds all placeholders in the format `{{parameter}}` within
    the template string and substitutes them with corresponding values from the
    `parameters` dictionary. If a placeholder does not have a corresponding key
    in the `parameters` dictionary, it will be left as is with a warning.

    Args:
        template: The template string containing placeholders.
        parameters: A dictionary where keys match placeholder names and values
            are the substitution content. Values will be stringified.

    Returns:
        The fully rendered template with all available placeholders replaced.
        Missing parameters will remain as placeholders.
    """
    if parameters is None:
        parameters = {}
    pattern = r"\{\{([^}]+)\}\}"

    def replacer(match):
        param_name = match.group(1).strip()
        if param_name not in parameters:
            warn(f"Missing parameter '{param_name}' in template")
            return ""
        return str(parameters[param_name])

    return sub(pattern, replacer, template)


def get_prompt(
    template_file: str,
    template_str: Optional[str] = None,
    parameters: Optional[Mapping[str, Any]] = None,
) -> str:
    """Generate a complete prompt from a template file and parameters.

    This function combines `load_template` and `render_template` into a single
    workflow. It first loads a template from a YAML file and then populates it
    with the provided parameters.

    Args:
        template_file: The path to the YAML template file.
        template_str: The nested path to the specific template within the file.
        parameters: A dictionary of key-value pairs for placeholder
            substitution.

    Returns:
        The final, rendered prompt string ready for use.

    Raises:
        Exceptions from both `load_template` and `render_template`.
    """
    if parameters is None:
        parameters = {}
    template = load_template(template_file, template_str)
    return render_template(template, parameters)


async def download_obs_file(
    obs_file: str,
    server_dir: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = SERVER_CONFIG.OBS_SERVER,
    bucket_name: str = SERVER_CONFIG.BUCKET_NAME,
    part_size: int = SERVER_CONFIG.PART_SIZE,
    task_num: int = SERVER_CONFIG.TASK_NUM,
    max_retries: int = SERVER_CONFIG.MAX_RETRIES,
) -> str:
    """Download a single file from Object Storage Service (OBS).

    This function downloads a file from a specified OBS bucket to a local
    directory. It includes a retry mechanism with exponential backoff for
    transient errors.

    Args:
        obs_file: The object key (path) of the file in the OBS bucket.
        server_dir: The local directory where the file will be downloaded.
        access_key_id: The access key ID for OBS authentication.
        secret_access_key: The secret access key for OBS authentication.
        obs_server: The server endpoint for the OBS.
        bucket_name: The name of the OBS bucket.
        part_size: The size of each part for multipart downloads.
        task_num: The number of concurrent tasks for multipart downloads.
        max_retries: The maximum number of retry attempts for a failed
            download.

    Returns:
        The local path to the downloaded file.

    Raises:
        OSError: If the file download fails after all retry attempts.
    """
    user_name = obs_file.split("/")[-2]
    server_path = Path(server_dir) / user_name / str(uuid1())
    server_path.mkdir(parents=True, exist_ok=True)
    server_file = str(server_path / Path(obs_file).name)

    object_key = obs_file
    if object_key.startswith(f"obs://{bucket_name}/"):
        object_key = object_key[len(f"obs://{bucket_name}/") :]
    elif object_key.startswith(f"/obs/{bucket_name}/"):
        object_key = object_key[len(f"/obs/{bucket_name}/") :]
    elif object_key.startswith(f"/{bucket_name}/"):
        object_key = object_key[len(f"/{bucket_name}/") :]
    elif object_key.startswith("/"):
        object_key = object_key[1:]

    obs_client = ObsClient(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        server=obs_server,
    )
    loop = asyncio.get_event_loop()
    for attempt in range(max_retries + 1):
        try:
            download_response = await loop.run_in_executor(
                None,
                lambda: obs_client.downloadFile(
                    bucketName=bucket_name,
                    objectKey=object_key,
                    downloadFile=server_file,
                    partSize=part_size,
                    taskNum=task_num,
                    enableCheckpoint=True,
                ),
            )
            if download_response.status < 300:
                return server_file
            raise OSError(
                f"Download File Failed\n"
                f"requestId: {download_response.requestId}\n"
                f"errorCode: {download_response.errorCode}\n"
                f"errorMessage: {download_response.errorMessage}"
            )
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(1.5**attempt)
                continue
            raise OSError(f"Download File Failed\n{format_exc()}") from exc
    return server_file


async def download_obs_list(
    obs_file_list: List[str],
    server_dir: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = SERVER_CONFIG.OBS_SERVER,
    bucket_name: str = SERVER_CONFIG.BUCKET_NAME,
    part_size: int = SERVER_CONFIG.PART_SIZE,
    task_num: int = SERVER_CONFIG.TASK_NUM,
    max_retries: int = SERVER_CONFIG.MAX_RETRIES,
    max_concurrency: int = SERVER_CONFIG.MAX_CONCURRENCY,
) -> List[str]:
    """Download multiple files from OBS concurrently.

    This function uses an `asyncio.Semaphore` to limit the number of
    concurrent downloads, improving performance and avoiding rate limits.

    Args:
        obs_file_list: A list of object keys (paths) for the files to be
            downloaded from OBS.
        server_dir: The local directory where the files will be downloaded.
        access_key_id: The access key ID for OBS authentication.
        secret_access_key: The secret access key for OBS authentication.
        obs_server: The server endpoint for the OBS.
        bucket_name: The name of the OBS bucket.
        part_size: The size of each part for multipart downloads.
        task_num: The number of concurrent tasks for multipart downloads.
        max_retries: The maximum number of retries for each failed download.
        max_concurrency: The maximum number of files to download in parallel.

    Returns:
        A list of local paths to the downloaded files.
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def download_with_semaphore(obs_file: str) -> str:
        async with semaphore:
            return await download_obs_file(
                obs_file=obs_file,
                server_dir=server_dir,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=obs_server,
                bucket_name=bucket_name,
                part_size=part_size,
                task_num=task_num,
                max_retries=max_retries,
            )

    tasks = [download_with_semaphore(obs_file) for obs_file in obs_file_list]
    return await asyncio.gather(*tasks)


def convert_single_file(server_file: str) -> str:
    """Convert a single file to Markdown format.

    This function uses the `MarkItDown` library to convert a file (e.g., PDF,
    DOCX) into Markdown text. The original file is deleted after conversion.

    Args:
        server_file: The local path to the file to be converted.

    Returns:
        A string containing the Markdown content of the converted file.
    """
    md_instance = MarkItDown(
        docintel_endpoint="<document_intelligence_endpoint>"
    )
    result = md_instance.convert(server_file)
    server_path = Path(server_file)
    server_path.unlink()
    return result.text_content


def convert_multi_files(
    server_file_list: List[str],
    max_workers: int = SERVER_CONFIG.MAX_WORKERS,
) -> List[str]:
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
    obs_file_list: List[str],
    server_dir: str,
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = SERVER_CONFIG.OBS_SERVER,
    bucket_name: str = SERVER_CONFIG.BUCKET_NAME,
    part_size: int = SERVER_CONFIG.PART_SIZE,
    task_num: int = SERVER_CONFIG.TASK_NUM,
    max_retries: int = SERVER_CONFIG.MAX_RETRIES,
    max_concurrency: int = SERVER_CONFIG.MAX_CONCURRENCY,
    max_workers: int = SERVER_CONFIG.MAX_WORKERS,
    executor: Optional[ProcessPoolExecutor] = None,
) -> List[str]:
    """Download, and convert multiple files from OBS in a parallel pipeline.

    This function orchestrates a workflow where files are downloaded from OBS
    concurrently and then converted to Markdown in a parallel process pool.
    It is designed for efficient batch processing of documents.

    Args:
        obs_file_list: A list of object keys for the files in OBS.
        server_dir: The local directory for temporary file storage.
        access_key_id: The access key ID for OBS authentication.
        secret_access_key: The secret access key for OBS authentication.
        obs_server: The server endpoint for the OBS.
        bucket_name: The name of the OBS bucket.
        part_size: The size of each part for multipart downloads.
        task_num: The number of concurrent tasks for multipart downloads.
        max_retries: The maximum number of retries for each failed operation.
        max_concurrency: The maximum number of files to download in parallel.
        max_workers: The maximum number of processes for file conversion.
        executor: An optional existing `ProcessPoolExecutor` to reuse for
            conversions. If None, a new one is created and managed.

    Returns:
        A list of strings, each containing the Markdown content of a
        processed file.
    """
    # semaphore = asyncio.Semaphore(max_concurrency)
    should_shutdown = executor is None
    if should_shutdown:
        executor = ProcessPoolExecutor(max_workers=max_workers)

    # Legacy: concurrent download and convert using asyncio.gather
    # async def download_and_convert(obs_file: str) -> str:
    #     async with semaphore:
    #         server_file = await download_obs_file(
    #             obs_file=obs_file,
    #             server_dir=server_dir,
    #             access_key_id=access_key_id,
    #             secret_access_key=secret_access_key,
    #             obs_server=obs_server,
    #             bucket_name=bucket_name,
    #             part_size=part_size,
    #             task_num=task_num,
    #             max_retries=max_retries,
    #         )
    #         loop = asyncio.get_event_loop()
    #         result = await loop.run_in_executor(
    #             executor, convert_single_file, server_file)
    #         return result

    # Default: sequential download and convert (more stable)
    async def download_and_convert(obs_file: str) -> str:
        server_file = await download_obs_file(
            obs_file=obs_file,
            server_dir=server_dir,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
            part_size=part_size,
            task_num=task_num,
            max_retries=max_retries,
        )
        result = convert_single_file(server_file)
        return result

    try:
        # Legacy (concurrent):
        # tasks = [download_and_convert(obs_file)
        #          for obs_file in obs_file_list]
        # return await asyncio.gather(*tasks)

        # Default (sequential):
        result = [
            await download_and_convert(obs_file) for obs_file in obs_file_list
        ]
        return result
    finally:
        if should_shutdown and executor is not None:
            executor.shutdown(wait=True)


def split_list(lst: List, max_size: int = 128) -> List[List]:
    """Split a list into evenly sized chunks.

    This function divides a list into a specified number of chunks, making
    their sizes as close as possible. This is useful for batch processing.

    Args:
        lst: The list to be split.
        max_size: The maximum size for any chunk.

    Returns:
        A list of lists, where each inner list is a chunk of the original.
        Returns an empty list if the input is empty.
    """
    n = len(lst)
    if n == 0:
        return []

    num_chunks = ceil(n / max_size)
    base_size = n // num_chunks
    remainder = n % num_chunks

    chunks = []
    index = 0
    for i in range(num_chunks):
        chunk_size = base_size + 1 if i < remainder else base_size
        chunks.append(lst[index : index + chunk_size])
        index += chunk_size
    return chunks
