# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helpers for tokens, prompts, OBS downloads, and cached file reads."""

import asyncio
import json
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from random import uniform
from re import sub
from traceback import format_exc
from typing import Any, List, Mapping, Optional
from uuid import uuid1
from warnings import warn

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPError,
    HTTPStatusError,
    Timeout,
    TimeoutException,
)
from markitdown import MarkItDown
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
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


@dataclass(frozen=True)
class ObsCredentials:
    """OBS credential pair for transfer helpers."""

    access_key_id: str = DEFAULT_ACCESS_KEY_ID
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY


@dataclass(frozen=True)
class ObsDownloadOptions:
    """OBS endpoint and retry options for file downloads."""

    obs_server: str = SERVER_CONFIG.OBS_SERVER
    bucket_name: str = SERVER_CONFIG.BUCKET_NAME
    part_size: int = SERVER_CONFIG.PART_SIZE
    task_num: int = SERVER_CONFIG.TASK_NUM
    max_retries: int = SERVER_CONFIG.MAX_RETRIES


@dataclass(frozen=True)
class ObsTransferContext:
    """Resolved OBS transfer settings used across download helpers."""

    server_dir: str
    credentials: ObsCredentials
    download: ObsDownloadOptions
    max_concurrency: int = SERVER_CONFIG.MAX_CONCURRENCY
    max_workers: int = SERVER_CONFIG.MAX_WORKERS


def message_content(response: Any) -> str:
    """Return the first assistant message content from an OpenAI-style dict."""
    if (
        isinstance(response, dict)
        and response.get("choices")
        and isinstance(response["choices"], list)
        and response["choices"][0]
        and isinstance(response["choices"][0], dict)
        and isinstance(response["choices"][0].get("message"), dict)
    ):
        return str(response["choices"][0]["message"].get("content", ""))
    return ""


async def retry_http_status_or_raise(
    exc: HTTPStatusError,
    *,
    attempt: int,
    max_retries: int,
    retriable_codes: Iterable[int],
    message: str,
) -> bool:
    """Sleep for a retriable HTTP status error or raise an MCP error."""
    if (
        exc.response is not None
        and exc.response.status_code in retriable_codes
        and attempt < max_retries
    ):
        await asyncio.sleep((2**attempt) + uniform(0, 1))
        return True
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"{message}: {str(exc)}",
        )
    ) from exc


async def retry_network_or_raise(
    exc: ConnectError | TimeoutException,
    *,
    attempt: int,
    max_retries: int,
    message: str = "Network error",
) -> bool:
    """Sleep for a retriable network error or raise an MCP error."""
    if attempt < max_retries:
        await asyncio.sleep(1.5**attempt)
        return True
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"{message}: {str(exc)}",
        )
    ) from exc


def parse_json_list_fragment(text: str) -> List[Any]:
    """Parse a JSON list embedded in model output text."""
    if not text:
        return []
    start_index = text.find("[")
    end_index = text.rfind("]") + 1
    if start_index == -1 or end_index <= start_index:
        return []
    try:
        parsed = json.loads(text[start_index:end_index])
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_follow_up_questions(text: str) -> List[str]:
    """Parse follow-up questions from a JSON list embedded in model output."""
    return parse_json_list_fragment(text)


def attach_message_payload(
    phyto_response: dict[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach payload fields to the first assistant message."""
    if not isinstance(phyto_response, dict) or "choices" not in phyto_response:
        phyto_response = {"choices": [{"message": {}}]}
    if not phyto_response["choices"]:
        phyto_response["choices"].append({"message": {}})
    if "message" not in phyto_response["choices"][0]:
        phyto_response["choices"][0]["message"] = {}

    phyto_response["choices"][0]["message"].update(dict(payload))
    return phyto_response


def join_limited_fragments(
    fragments: Iterable[str],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Join fragments until their combined length reaches the limit."""
    selected_fragments = []
    total_length = initial_length
    for fragment in fragments:
        if total_length + len(fragment) <= max_tokens:
            selected_fragments.append(fragment)
            total_length += len(fragment)
        else:
            break
    return "\n\n".join(selected_fragments), total_length


def format_upload_context(
    upload_texts: Iterable[str],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Format uploaded file texts as bounded prompt context."""
    fragments = (
        f"[user upload file {index + 1} begin]\n"
        f"{text}\n[user upload file {index + 1} end]"
        for index, text in enumerate(upload_texts)
    )
    return join_limited_fragments(
        fragments,
        max_tokens=max_tokens,
        initial_length=initial_length,
    )


def format_retrieved_doc_context(
    docs: Iterable[Mapping[str, Any]],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Format retrieved documents as bounded prompt context."""
    fragments = (
        _format_retrieved_doc_fragment(doc, index)
        for index, doc in enumerate(docs)
    )
    return join_limited_fragments(
        fragments,
        max_tokens=max_tokens,
        initial_length=initial_length,
    )


def _format_retrieved_doc_fragment(
    doc: Mapping[str, Any],
    index: int,
) -> str:
    header = f"[document {index + 1} begin] {doc['title']}"
    content_field = (
        doc.get("big_content")
        if "big_content" in doc
        else doc.get("content", "")
    )
    body = (
        f"{doc['subtitle']}\n{content_field}"
        if doc.get("subtitle")
        else doc.get("content", "")
    )
    return f"{header}\n{body} [document {index + 1} end]"


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
    if isinstance(data, dict) and len(data) == 1:
        return next(iter(data.values()))
    raise ValueError("Must specify template_str for multi-level templates")


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
    context = _obs_transfer_context(server_dir, kwargs)
    user_name = obs_file.split("/")[-2]
    server_path = Path(context.server_dir) / user_name / str(uuid1())
    server_path.mkdir(parents=True, exist_ok=True)
    server_file = str(server_path / Path(obs_file).name)

    obs_client = ObsClient(
        access_key_id=context.credentials.access_key_id,
        secret_access_key=context.credentials.secret_access_key,
        server=context.download.obs_server,
    )
    object_key = _obs_object_key(obs_file, context.download.bucket_name)
    return await _download_obs_file_with_retry(
        obs_client,
        object_key,
        server_file,
        context,
    )


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
        credentials=ObsCredentials(
            access_key_id=values.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
            secret_access_key=values.get(
                "secret_access_key",
                DEFAULT_SECRET_ACCESS_KEY,
            ),
        ),
        download=ObsDownloadOptions(
            obs_server=values.get("obs_server", SERVER_CONFIG.OBS_SERVER),
            bucket_name=values.get("bucket_name", SERVER_CONFIG.BUCKET_NAME),
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
    object_key = obs_file
    if object_key.startswith(f"obs://{bucket_name}/"):
        return object_key[len(f"obs://{bucket_name}/") :]
    if object_key.startswith(f"/obs/{bucket_name}/"):
        return object_key[len(f"/obs/{bucket_name}/") :]
    if object_key.startswith(f"/{bucket_name}/"):
        return object_key[len(f"/{bucket_name}/") :]
    if object_key.startswith("/"):
        return object_key[1:]
    return object_key


async def _download_obs_file_with_retry(
    obs_client: ObsClient,
    object_key: str,
    server_file: str,
    context: ObsTransferContext,
) -> str:
    """Download one OBS object with retry and backoff."""
    for attempt in range(context.download.max_retries + 1):
        try:
            download_response = await _download_obs_file_once(
                obs_client,
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
            raise OSError(f"Download File Failed\n{format_exc()}") from exc
    return server_file


async def _download_obs_file_once(
    obs_client: ObsClient,
    object_key: str,
    server_file: str,
    context: ObsTransferContext,
):
    """Run one blocking OBS download in the default executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: obs_client.downloadFile(
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
    obs_file_list: List[str],
    server_dir: str,
    **kwargs: Any,
) -> List[str]:
    """Download multiple files from OBS concurrently.

    This function uses an `asyncio.Semaphore` to limit the number of
    concurrent downloads, improving performance and avoiding rate limits.

    Args:
        obs_file_list: A list of object keys (paths) for the files to be
            downloaded from OBS.
        server_dir: The local directory where the files will be downloaded.
        **kwargs: Keyword-compatible OBS transfer overrides.

    Returns:
        A list of local paths to the downloaded files.
    """
    context = _obs_transfer_context(server_dir, kwargs)
    semaphore = asyncio.Semaphore(context.max_concurrency)

    async def download_with_semaphore(obs_file: str) -> str:
        async with semaphore:
            return await download_obs_file(
                obs_file=obs_file,
                server_dir=context.server_dir,
                transfer_context=context,
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
    executor: Optional[ProcessPoolExecutor] = None,
    **kwargs: Any,
) -> List[str]:
    """Download, and convert multiple files from OBS in a parallel pipeline.

    This function orchestrates a workflow where files are downloaded from OBS
    concurrently and then converted to Markdown in a parallel process pool.
    It is designed for efficient batch processing of documents.

    Args:
        obs_file_list: A list of object keys for the files in OBS.
        server_dir: The local directory for temporary file storage.
        executor: An optional existing `ProcessPoolExecutor` to reuse for
            conversions. If None, a new one is created and managed.
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
    executor: ProcessPoolExecutor,
) -> str:
    """Download one OBS file and convert it to Markdown."""
    server_file = await download_obs_file(
        obs_file=obs_file,
        server_dir=context.server_dir,
        transfer_context=context,
    )
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, convert_single_file, server_file
    )


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
