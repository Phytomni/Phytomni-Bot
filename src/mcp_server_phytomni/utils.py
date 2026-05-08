# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared helper compatibility exports and list utilities."""

from math import ceil
from typing import List

from .auth.iam import get_token
from .common.docs import (
    format_retrieved_doc_context,
    format_retrieved_doc_fragment,
    format_upload_context,
)
from .common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
    request_response_with_retries,
    retry_http_status_or_raise,
    retry_network_or_raise,
)
from .common.prompts import (
    file_cache_fingerprint,
    get_prompt,
    load_json_file,
    load_template,
    load_text_file,
    render_template,
)
from .common.responses import (
    attach_message_payload,
    first_message,
    join_limited_fragments,
    message_content,
    parse_follow_up_questions,
    parse_json_list_fragment,
)
from .storage.downloads import (
    ObsCredentials,
    ObsDownloadOptions,
    ObsTransferContext,
    ResolvedObsFile,
    convert_multi_files,
    convert_single_file,
    download_list_convert,
    download_obs_file,
    download_obs_list,
    download_upload_context,
)

__all__ = [
    "JsonPostRequest",
    "JsonPostRetry",
    "attach_message_payload",
    "file_cache_fingerprint",
    "first_message",
    "format_retrieved_doc_context",
    "format_retrieved_doc_fragment",
    "format_upload_context",
    "get_prompt",
    "get_token",
    "join_limited_fragments",
    "load_json_file",
    "load_template",
    "load_text_file",
    "message_content",
    "parse_follow_up_questions",
    "parse_json_list_fragment",
    "post_json_with_retries",
    "render_template",
    "request_response_with_retries",
    "retry_http_status_or_raise",
    "retry_network_or_raise",
    "split_list",
    "download_upload_context",
    "download_obs_list",
    "download_obs_file",
    "download_list_convert",
    "convert_single_file",
    "convert_multi_files",
    "ResolvedObsFile",
    "ObsTransferContext",
    "ObsDownloadOptions",
    "ObsCredentials",
]


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
