# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides a client for interacting with Phyto language models.

It includes a function to generate text based on a user query and a system
prompt, with support for various model parameters and retry mechanisms.
"""

import asyncio
from typing import Any, Dict, List, Optional

from httpx import ConnectError, HTTPStatusError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
from openai import AsyncOpenAI

from ...common.docs import format_upload_context
from ...common.http import (
    retry_http_status_or_raise,
    retry_network_or_raise,
)
from ...common.prompts import get_prompt
from ...common.responses import (
    first_message,
    message_content,
    parse_follow_up_questions,
)
from ...config.defaults import ChatConfig
from ...config.settings import SensitiveConfig
from ...storage.downloads import download_list_convert

CHAT_CONFIG = ChatConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)


async def phyto_chat_with_follow(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Generate text using a Phyto language model with optional file context.

    This function sends a request to a Phyto language model and returns the
    generated text. It supports various model parameters, optional file uploads
    from OBS, and includes a retry mechanism with exponential backoff for
    transient errors. Files are downloaded, converted to markdown, and
    integrated into the user query as context.

    Args:
        user_query: The user's natural language query or instruction.
        obs_file_list: List of OBS object keys (file paths) to download and
            include as context in the query. Files are converted to markdown.
        prompt_file: Path to the YAML template file containing system prompts.
        prompt_path: Nested path within the template file to locate the
            specific system prompt (e.g., "system/ai4ps").
        api_key: API key for authenticating with the language model service.
        base_url: Base URL endpoint for the language model API service.
        model: Identifier of the specific language model to use for generation.
        frequency_penalty: Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        n: Number of completion choices to generate for each input.
        presence_penalty: Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        reasoning_effort: Level of reasoning effort for the language model.
            Typically 'low', 'medium', or 'high'.
        response_format: Desired response format from the language model.
            For example, {'type': 'json_object'} to request JSON response.
        stream: Flag to enable or disable streaming of responses from the
            language model. If True, responses are sent as a series of events.
        temperature: Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        top_p: Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to top_p.
        user: User identifier for API interactions, particularly for chat or
            language model services.
        server_dir: Local directory path for temporary file storage during
            file downloads and processing.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket containing the files.
        part_size: Size of each part for multipart downloads from OBS.
        task_num: Number of concurrent tasks for multipart downloads from OBS.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries
            for API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_concurrency: Maximum number of files to download from OBS
            concurrently.
        max_workers: Maximum number of worker processes to use for file
            conversion operations.
        max_tokens: Maximum number of tokens to generate in language model
            responses.
        semaphore: Optional asyncio.Semaphore to limit concurrent execution
            of this function. Useful for controlling resource usage.

    Returns:
        A dictionary containing the complete API response from the language
        model, including generated text, usage statistics, and metadata.

    Raises:
        McpError: If the API call fails after all retry attempts, or if
            file download/conversion operations fail.

    Examples:
        Basic text generation:
            >>> result = await phyto_chat("What is photosynthesis?")
            >>> print(result['choices'][0]['message']['content'])

        With file context:
            >>> files = ["/obs/bucket/research_paper.pdf"]
            >>> result = await phyto_chat(
            ...     "Summarize this paper",
            ...     obs_file_list=files
            ... )
    """
    prompt_file = kwargs.get("prompt_file", CHAT_CONFIG.PROMPT_FILE)

    phyto_response = await phyto_chat(
        user_query=user_query,
        obs_file_list=obs_file_list,
        semaphore=semaphore,
        **kwargs,
    )

    system_response_content = message_content(phyto_response)

    follow_kwargs = {**kwargs, "prompt_file": prompt_file}
    follow_up_response = await phyto_chat(
        user_query=get_prompt(
            prompt_file,
            "system/follow_up_questions",
            {
                "user_query": user_query,
                "system_response": system_response_content,
            },
        ),
        semaphore=semaphore,
        **follow_kwargs,
    )

    follow_up_list = parse_follow_up_questions(
        message_content(follow_up_response)
    )

    message = first_message(phyto_response)
    if message is not None:
        message.update({"follow_up_questions": follow_up_list})

    return phyto_response


async def phyto_chat(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Generate text using a Phyto language model with optional file context.

    This function sends a request to a Phyto language model and returns the
    generated text. It supports various model parameters, optional file uploads
    from OBS, and includes a retry mechanism with exponential backoff for
    transient errors. Files are downloaded, converted to markdown, and
    integrated into the user query as context.

    Args:
        user_query: The user's natural language query or instruction.
        obs_file_list: List of OBS object keys (file paths) to download and
            include as context in the query. Files are converted to markdown.
        prompt_file: Path to the YAML template file containing system prompts.
        prompt_path: Nested path within the template file to locate the
            specific system prompt (e.g., "system/ai4ps").
        api_key: API key for authenticating with the language model service.
        base_url: Base URL endpoint for the language model API service.
        model: Identifier of the specific language model to use for generation.
        frequency_penalty: Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        n: Number of completion choices to generate for each input.
        presence_penalty: Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        reasoning_effort: Level of reasoning effort for the language model.
            Typically 'low', 'medium', or 'high'.
        response_format: Desired response format from the language model.
            For example, {'type': 'json_object'} to request JSON response.
        stream: Flag to enable or disable streaming of responses from the
            language model. If True, responses are sent as a series of events.
        temperature: Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        top_p: Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to top_p.
        user: User identifier for API interactions, particularly for chat or
            language model services.
        server_dir: Local directory path for temporary file storage during
            file downloads and processing.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket containing the files.
        part_size: Size of each part for multipart downloads from OBS.
        task_num: Number of concurrent tasks for multipart downloads from OBS.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries
            for API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_concurrency: Maximum number of files to download from OBS
            concurrently.
        max_workers: Maximum number of worker processes to use for file
            conversion operations.
        max_tokens: Maximum number of tokens to generate in language model
            responses.
        semaphore: Optional asyncio.Semaphore to limit concurrent execution
            of this function. Useful for controlling resource usage.

    Returns:
        A dictionary containing the complete API response from the language
        model, including generated text, usage statistics, and metadata.

    Raises:
        McpError: If the API call fails after all retry attempts, or if
            file download/conversion operations fail.

    Examples:
        Basic text generation:
            >>> result = await phyto_chat("What is photosynthesis?")
            >>> print(result['choices'][0]['message']['content'])

        With file context:
            >>> files = ["/obs/bucket/research_paper.pdf"]
            >>> result = await phyto_chat(
            ...     "Summarize this paper",
            ...     obs_file_list=files
            ... )
    """
    if obs_file_list is None:
        obs_file_list = []
    else:
        obs_file_list = list(obs_file_list)
    options = _chat_options(kwargs)
    if obs_file_list:
        user_query = await _query_with_upload_context(
            user_query,
            obs_file_list,
            options,
        )
    messages = [
        {
            "role": "system",
            "content": get_prompt(
                options["prompt_file"], options["prompt_path"]
            ),
        },
        {
            "role": "user",
            "content": user_query,
        },
    ]

    if semaphore is not None:
        async with semaphore:
            return await _run_phyto_chat(messages, options)
    return await _run_phyto_chat(messages, options)


def _chat_options(values: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve keyword-compatible chat and OBS options."""
    response_format = values.get("response_format")
    retriable_codes = values.get("retriable_codes")
    return {
        "prompt_file": values.get("prompt_file", CHAT_CONFIG.PROMPT_FILE),
        "prompt_path": values.get("prompt_path", CHAT_CONFIG.PROMPT_PATH),
        "api_key": values.get(
            "api_key", SENSITIVE_CONFIG.API_KEY.get_secret_value()
        ),
        "base_url": values.get("base_url", SENSITIVE_CONFIG.BASE_URL),
        "model": values.get("model", SENSITIVE_CONFIG.MODEL_ID),
        "frequency_penalty": values.get(
            "frequency_penalty", CHAT_CONFIG.FREQUENCY_PENALTY
        ),
        "n": values.get("n", CHAT_CONFIG.N),
        "presence_penalty": values.get(
            "presence_penalty", CHAT_CONFIG.PRESENCE_PENALTY
        ),
        "reasoning_effort": values.get(
            "reasoning_effort", CHAT_CONFIG.REASONING_EFFORT
        ),
        "response_format": (
            dict(CHAT_CONFIG.RESPONSE_FORMAT)
            if response_format is None
            else dict(response_format)
        ),
        "stream": values.get("stream", CHAT_CONFIG.STREAM),
        "temperature": values.get("temperature", CHAT_CONFIG.TEMPERATURE),
        "top_p": values.get("top_p", CHAT_CONFIG.TOP_P),
        "user": values.get("user", CHAT_CONFIG.USER),
        "server_dir": values.get("server_dir", CHAT_CONFIG.TEMP_DIR),
        "access_key_id": values.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        "secret_access_key": values.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        "obs_server": values.get("obs_server", CHAT_CONFIG.OBS_SERVER),
        "bucket_name": values.get("bucket_name", CHAT_CONFIG.BUCKET_NAME),
        "part_size": values.get("part_size", CHAT_CONFIG.PART_SIZE),
        "task_num": values.get("task_num", CHAT_CONFIG.TASK_NUM),
        "timeout": values.get("timeout", CHAT_CONFIG.TIMEOUT),
        "retriable_codes": (
            list(CHAT_CONFIG.RETRIABLE_CODES)
            if retriable_codes is None
            else list(retriable_codes)
        ),
        "max_retries": values.get("max_retries", CHAT_CONFIG.MAX_RETRIES),
        "max_concurrency": values.get(
            "max_concurrency", CHAT_CONFIG.MAX_CONCURRENCY
        ),
        "max_workers": values.get("max_workers", CHAT_CONFIG.MAX_WORKERS),
        "max_tokens": values.get("max_tokens", CHAT_CONFIG.MAX_TOKENS),
    }


async def _query_with_upload_context(
    user_query: str,
    obs_file_list: List[str],
    options: Dict[str, Any],
) -> str:
    """Download uploaded files and prepend bounded context to the query."""
    upload_str_list = await download_list_convert(
        obs_file_list=obs_file_list,
        server_dir=options["server_dir"],
        access_key_id=options["access_key_id"],
        secret_access_key=options["secret_access_key"],
        obs_server=options["obs_server"],
        bucket_name=options["bucket_name"],
        part_size=options["part_size"],
        task_num=options["task_num"],
        max_retries=options["max_retries"],
        max_concurrency=options["max_concurrency"],
        max_workers=options["max_workers"],
    )
    upload_context, _ = format_upload_context(
        upload_str_list,
        options["max_tokens"],
    )
    return (
        "Based on the following files uploaded by the user:\n"
        f"{upload_context}\n"
        f"Please answer the user's questions:\n{user_query}"
    )


def _completion_params(
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Return OpenAI chat completion parameters."""
    params: Dict[str, Any] = {
        "messages": messages,
        "model": options["model"],
        "frequency_penalty": options["frequency_penalty"],
        "n": options["n"],
        "presence_penalty": options["presence_penalty"],
        "response_format": options["response_format"],
        "stream": options["stream"],
        "temperature": options["temperature"],
        "top_p": options["top_p"],
        "user": options["user"],
        "timeout": options["timeout"],
    }
    if options["max_tokens"] is not None:
        params["max_tokens"] = options["max_tokens"]
    if (
        "reasoner" in options["model"]
        and options["reasoning_effort"] is not None
    ):
        params["reasoning_effort"] = options["reasoning_effort"]
    return params


async def _run_phyto_chat(
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Call the Phyto chat endpoint with retry handling."""
    client = AsyncOpenAI(
        api_key=options["api_key"], base_url=options["base_url"]
    )
    for attempt in range(options["max_retries"] + 1):
        try:
            params = _completion_params(messages, options)
            chat_completions = await client.chat.completions.create(**params)
            if options["stream"]:
                return await _stream_response_to_dict(chat_completions)
            return chat_completions.model_dump()
        except HTTPStatusError as exc:
            if await retry_http_status_or_raise(
                exc,
                attempt=attempt,
                max_retries=options["max_retries"],
                retriable_codes=options["retriable_codes"],
                message="Failed to generate from Phyto",
            ):
                continue
        except (ConnectError, TimeoutException) as exc:
            if await retry_network_or_raise(
                exc,
                attempt=attempt,
                max_retries=options["max_retries"],
            ):
                continue
    return None


async def _stream_response_to_dict(stream_completions: Any) -> Dict[str, Any]:
    """Collect streaming chunks into an OpenAI-style response dictionary."""
    full_content = ""
    chunk = None
    async for chunk in stream_completions:
        if chunk.choices and chunk.choices[0].delta.content:
            full_content += chunk.choices[0].delta.content
    if chunk is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="No response received from model",
            )
        )
    chat_completions = chunk.model_dump()
    chat_completions.update({"choices": [_stream_choice(full_content)]})
    return chat_completions


def _stream_choice(full_content: str) -> Dict[str, Any]:
    """Return the normalized final streaming choice."""
    return {
        "finish_reason": "stop",
        "index": 0,
        "logprobs": None,
        "message": {
            "content": full_content.strip(),
            "refusal": None,
            "role": "assistant",
            "annotations": None,
            "audio": None,
            "function_call": None,
            "tool_calls": [],
        },
        "stop_reason": None,
    }
