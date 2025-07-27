# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""A collection of utility functions for shared services.

This module provides a variety of helper functions that support other modules
within the application. These utilities include functionalities such as:
- Authenticating and retrieving API tokens.
- Loading and rendering text-based templates from YAML files.
- Asynchronously downloading files from an Object Storage Service (OBS) with
  concurrency control and retry mechanisms.
- Converting various file formats to Markdown.
- Orchestrating complex asynchronous workflows that involve downloading and
  processing multiple files.
- Splitting lists into smaller chunks for batch processing.
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from json import dumps
from math import ceil
from pathlib import Path
from re import sub
from traceback import format_exc
from typing import Dict, List, Optional
from yaml import safe_load

from httpx import AsyncClient, HTTPError, Timeout
from markitdown import MarkItDown
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import ObsClient

from .config.defaults import ServerConfig
from .config.settings import SensitiveConfig

serc = ServerConfig()
senc = SensitiveConfig().load()


async def get_token(timeout: float = serc.TIMEOUT,
                    region: str = serc.REGION) -> str:
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
        password = senc.USER_PASSWORD.get_secret_value()
        data = {
            'auth': {
                'identity': {
                    'methods': ['password'],
                    'password': {
                        'user': {
                            'name': senc.USER_NAME,
                            'password': password,
                            'domain': {'name': senc.DOMAIN_NAME},
                        },
                    },
                },
                'scope': {'project': {'name': region}},
            },
        }
        try:
            response = await client.post(
                serc.TOKEN_URL,
                headers={'Content-Type': 'application/json'},
                data=dumps(data),
                timeout=timeout)
            response.raise_for_status()
            return response.headers['X-Subject-Token']
        except HTTPError as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f'Failed to get token: {str(e)}')) from e


def load_template(template_file: str,
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
    template_file = Path(template_file)
    if not template_file.is_file():
        raise FileNotFoundError(f'Template file not found: {template_file}')
    with open(template_file, 'r', encoding='utf-8') as f:
        data = safe_load(f)
    if template_str:
        current = data
        for part in template_str.split('/'):
            if part not in current:
                raise KeyError(
                    f"Path '{part}' not found in template structure")
            current = current[part]
        return current
    else:
        if isinstance(data, dict) and len(data) == 1:
            return next(iter(data.values()))
        else:
            raise ValueError(
                'Must specify template_str for multi-level templates')


def render_template(template: str,
                    parameters: Optional[Dict[str, str]] = None
                    ) -> str:
    """Replace placeholders in a template string with provided values.

    This function finds all placeholders in the format `{{parameter}}` within
    the template string and substitutes them with corresponding values from the
    `parameters` dictionary.

    Args:
        template: The template string containing placeholders.
        parameters: A dictionary where keys match placeholder names and values
            are the substitution content. Values will be stringified.

    Returns:
        The fully rendered template with all placeholders replaced.

    Raises:
        ValueError: If a placeholder in the template does not have a
            corresponding key in the `parameters` dictionary.
    """
    if parameters is None:
        parameters = {}
    pattern = r'\{\{([^}]+)\}\}'

    def replacer(match):
        param_name = match.group(1).strip()
        if param_name not in parameters:
            raise ValueError(f'Missing parameter: {param_name}')
        return str(parameters[param_name])

    return sub(pattern, replacer, template)


def get_prompt(template_file: str,
               template_str: Optional[str] = None,
               parameters: Optional[Dict[str, str]] = None,
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
    access_key_id: str = senc.AccessKeyID.get_secret_value(),
    secret_access_key: str = senc.SecretAccessKey.get_secret_value(),
    obs_server: str = serc.OBS_SERVER,
    bucket_name: str = serc.BUCKET_NAME,
    part_size: int = serc.PART_SIZT,
    task_num: int = serc.TASK_NUM,
    max_retries: int = serc.MAX_RETRIES,
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
    server_path = Path(server_dir)
    server_path.mkdir(parents=True, exist_ok=True)
    server_file = str(server_path / Path(obs_file).name)

    object_key = obs_file
    if object_key.startswith(f'/{bucket_name}/'):
        object_key = object_key[len(f'/{bucket_name}/'):]
    elif object_key.startswith(f'/obs/{bucket_name}/'):
        object_key = object_key[len(f'/obs/{bucket_name}/'):]
    elif object_key.startswith('/'):
        object_key = object_key[1:]

    obs_client = ObsClient(access_key_id=access_key_id,
                           secret_access_key=secret_access_key,
                           server=obs_server)
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
            raise OSError(f'Download File Failed\n'
                          f'requestId: {download_response.requestId}\n'
                          f'errorCode: {download_response.errorCode}\n'
                          f'errorMessage: {download_response.errorMessage}')
        except Exception as exc:
            if attempt < max_retries:
                await asyncio.sleep(1.5 ** attempt)
                continue
            raise OSError(f'Download File Failed\n{format_exc()}') from exc


async def download_obs_list(
    obs_file_list: List[str],
    server_dir: str,
    access_key_id: str = senc.AccessKeyID.get_secret_value(),
    secret_access_key: str = senc.SecretAccessKey.get_secret_value(),
    obs_server: str = serc.OBS_SERVER,
    bucket_name: str = serc.BUCKET_NAME,
    part_size: int = serc.PART_SIZT,
    task_num: int = serc.TASK_NUM,
    max_retries: int = serc.MAX_RETRIES,
    max_concurrency: int = serc.MAX_CONCURRENCY,
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

    tasks = [download_with_semaphore(obs_file)
             for obs_file in obs_file_list]
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
        docintel_endpoint='<document_intelligence_endpoint>')
    result = md_instance.convert(server_file)
    server_path = Path(server_file)
    server_path.unlink()
    return result


def convert_multi_files(
    server_file_list: List[str],
    max_workers: int = serc.MAX_WORKERS,
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
    access_key_id: str = senc.AccessKeyID.get_secret_value(),
    secret_access_key: str = senc.SecretAccessKey.get_secret_value(),
    obs_server: str = serc.OBS_SERVER,
    bucket_name: str = serc.BUCKET_NAME,
    part_size: int = serc.PART_SIZT,
    task_num: int = serc.TASK_NUM,
    max_retries: int = serc.MAX_RETRIES,
    max_concurrency: int = serc.MAX_CONCURRENCY,
    max_workers: int = serc.MAX_WORKERS,
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
    semaphore = asyncio.Semaphore(max_concurrency)
    should_shutdown = executor is None
    if should_shutdown:
        executor = ProcessPoolExecutor(max_workers=max_workers)

    async def download_and_convert(obs_file: str) -> str:
        async with semaphore:
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
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, convert_single_file,
                                                server_file)
            return result

    try:
        tasks = [download_and_convert(obs_file) for obs_file in obs_file_list]
        return await asyncio.gather(*tasks)
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
        chunks.append(lst[index:index+chunk_size])
        index += chunk_size
    return chunks
