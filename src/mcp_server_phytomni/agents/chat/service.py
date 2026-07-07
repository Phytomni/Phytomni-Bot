# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides a client for interacting with Phyto language models.

It includes a function to generate text based on a user query and a system
prompt, with support for various model parameters and retry mechanisms.
"""

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

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
from ...common.reasoning_content import normalize_chat_completion_dict
from ...common.responses import (
    first_message,
    message_content,
    parse_follow_up_questions,
)
from ...config.defaults import ChatConfig
from ...config.relay_mode import relay_mode_enabled
from ...config.settings import get_sensitive_config
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...runtime.langgraph_runner import ainvoke_graph
from ...storage.downloads import download_list_convert

logger = logging.getLogger(__name__)

CHAT_CONFIG = ChatConfig()

# Retry budget for opening one streaming chat-completion connection.
# Once ``client.chat.completions.create(stream=True, ...)`` returns
# the iterator, any mid-stream failure propagates immediately — a
# silent retry would lose already-emitted chunks and corrupt the SSE
# timeline from the client's point of view.
MAX_OPEN_STREAM_RETRIES = 1


async def phyto_chat_with_follow(
    user_query: str,
    obs_file_list: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
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
    obs_file_list: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
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
    chat_kwargs: dict[str, Any] = {**kwargs, "with_follow_up": False}
    if semaphore is not None:
        chat_kwargs["semaphore"] = semaphore
    initial_state: dict[str, Any] = {
        "user_query": user_query,
        "obs_file_list": list(obs_file_list) if obs_file_list else [],
        "chat_kwargs": chat_kwargs,
    }
    final_state = await ainvoke_graph(_cached_chat_app(), initial_state)
    return final_state.get("response")


@lru_cache(maxsize=1)
def _cached_chat_app() -> Any:
    """Lazy singleton of the compiled chat subgraph.

    Resolves ``builder`` dynamically via ``importlib.import_module``
    rather than ``from .builder import _build_chat_graph``: a
    top-level from-import would close the service → builder → graph
    → service cycle at parse time (graph.py imports this module for
    helpers and late-bound ``phyto_chat`` / ``get_prompt`` lookups).
    ``importlib`` is opaque to pylint's R0401 cycle detector, which
    is the correct read since this edge IS dynamic: the import only
    fires once per process at first call, after every module above
    has finished loading. ``lru_cache`` makes the compile happen at
    most once and gives test suites a ``cache_clear()`` hook.
    """
    builder_module = importlib.import_module(".builder", package=__package__)
    # pylint: disable-next=protected-access
    return builder_module._build_chat_graph()


def _chat_options(values: dict[str, Any]) -> dict[str, Any]:
    """Resolve keyword-compatible chat and OBS options."""
    sensitive = get_sensitive_config()
    default_access_key_id, default_secret_access_key = (
        sensitive.obs_credentials()
    )
    response_format = values.get("response_format")
    retriable_codes = values.get("retriable_codes")
    return {
        "prompt_file": values.get("prompt_file", CHAT_CONFIG.PROMPT_FILE),
        "prompt_path": values.get("prompt_path", CHAT_CONFIG.PROMPT_PATH),
        "api_key": values.get("api_key", sensitive.API_KEY.get_secret_value()),
        "base_url": values.get("base_url", sensitive.BASE_URL),
        "model": values.get("model", sensitive.MODEL_ID),
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
        "access_key_id": values.get("access_key_id", default_access_key_id),
        "secret_access_key": values.get(
            "secret_access_key", default_secret_access_key
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
    obs_file_list: list[str],
    options: dict[str, Any],
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


def _relay_llm_endpoint(api_key: str, base_url: str) -> tuple[str, str]:
    """Override the LLM endpoint with the relay route in relay mode.

    In customer relay mode the child Bot holds no operator LLM
    credentials, so regardless of the ``api_key`` / ``base_url`` a caller
    passed (callers such as ``research/agent.py`` pass operator creds
    explicitly), point AsyncOpenAI at the relay ``/v1/relay/llm`` route
    authenticated by the relay key; the relay forwards to the operator
    LLM with its real credentials. Outside relay mode the caller's values
    pass through unchanged. ``RELAY_BASE_URL`` is read from a fresh
    ``ChatConfig()`` so it reflects the current environment rather than
    the import-time module default.

    Args:
        api_key: The operator API key the caller resolved.
        base_url: The operator base URL the caller resolved.

    Returns:
        ``(api_key, base_url)`` — relay values in relay mode, otherwise
        the inputs unchanged.
    """
    if not relay_mode_enabled():
        return api_key, base_url
    relay_key = get_sensitive_config().RELAY_API_KEY.get_secret_value()
    return relay_key, f"{ChatConfig().RELAY_BASE_URL}/v1/relay/llm"


@func_cache(
    key_params=[
        "messages",
        "response_format",
    ],
    ttl=LONG_TTL_SECONDS,
)
# pylint: disable=too-many-arguments,too-many-locals
# run_phyto_chat_cached is a @func_cache chokepoint: every named
# parameter goes into the cache key, so semantic LLM inputs must
# stay flat (no options-object wrapping) or rotating an API key would
# silently invalidate the cache. See docs/development/lint-exemptions.md.
async def run_phyto_chat_cached(
    *,
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    top_p: float,
    frequency_penalty: float,
    presence_penalty: float,
    n: int,
    max_tokens: int | None,
    response_format: dict[str, Any],
    reasoning_effort: str | None,
    api_key: str,
    base_url: str,
    user: str,
    timeout: float,
    stream: bool,
) -> dict[str, Any]:
    """Issue one LLM completion and cache the normalized dict.

    The cache key is the semantic sampling shape (messages, model, and
    sampling parameters). Infrastructure params (api_key, base_url,
    user, timeout, stream) are deliberately excluded so rotating an
    API key, swapping endpoints, or flipping streaming behavior does
    not invalidate the cache; identical sampling inputs share one
    stored answer across all infra variations.

    Any failure (HTTP error, transport error) propagates as an
    exception so the cache never persists a None or partial result —
    the outer retry/dispatcher layer owns the retry-exhaustion path
    and the None contract callers depend on.
    """
    api_key, base_url = _relay_llm_endpoint(api_key, base_url)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    params: dict[str, Any] = {
        "messages": messages,
        "model": model,
        "frequency_penalty": frequency_penalty,
        "n": n,
        "presence_penalty": presence_penalty,
        "response_format": response_format,
        "stream": stream,
        "temperature": temperature,
        "top_p": top_p,
        "user": user,
        "timeout": timeout,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if "reasoner" in model and reasoning_effort is not None:
        params["reasoning_effort"] = reasoning_effort
    chat_completions = await client.chat.completions.create(**params)
    if stream:
        payload = await _stream_response_to_dict(chat_completions)
    else:
        payload = chat_completions.model_dump()
    return normalize_chat_completion_dict(payload)


# pylint: enable=too-many-arguments,too-many-locals


async def _run_phyto_chat(
    messages: list[dict[str, str]],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Call the Phyto chat endpoint with retry handling.

    Thin dispatcher around ``run_phyto_chat_cached`` that owns the
    retry loop. The cached inner handles a single attempt and raises
    on any exception so the cache never stores a failure. Both retry
    helpers (``retry_http_status_or_raise`` /
    ``retry_network_or_raise``) either return True (sleep + retry) or
    raise ``McpError`` when retries are exhausted or the failure is
    non-retriable, so this function either returns a Dict from a
    successful attempt or propagates the helper's McpError.
    """
    for attempt in range(options["max_retries"] + 1):
        try:
            return await run_phyto_chat_cached(
                messages=messages,
                model=options["model"],
                temperature=options["temperature"],
                top_p=options["top_p"],
                frequency_penalty=options["frequency_penalty"],
                presence_penalty=options["presence_penalty"],
                n=options["n"],
                max_tokens=options["max_tokens"],
                response_format=options["response_format"],
                reasoning_effort=options["reasoning_effort"],
                api_key=options["api_key"],
                base_url=options["base_url"],
                user=options["user"],
                timeout=options["timeout"],
                stream=options["stream"],
            )
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
    # Unreachable: every loop iteration either returns from the try
    # block or raises through a retry helper on the final attempt. A
    # bare RuntimeError keeps the type checker honest and converts a
    # future regression (e.g. a retry helper that gains a False return
    # path) into a loud crash instead of a silent None propagating
    # through downstream agents.
    raise RuntimeError(
        "_run_phyto_chat fell through the retry loop; "
        "retry helpers must raise on the last attempt"
    )


async def stream_phyto_chat_chunks(
    user_query: str,
    obs_file_list: list[str] | None = None,
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Yield raw provider chunks for one streaming chat completion.

    Mirrors :func:`phyto_chat` for prompt construction and OBS upload
    context but deliberately bypasses :func:`run_phyto_chat_cached`:
    that primitive accepts ``stream: bool`` and buffers the full
    response into ``func_cache`` once collected, which is incompatible
    with a real token stream. This function calls
    ``AsyncOpenAI(...).chat.completions.create(stream=True, ...)``
    directly and yields each chunk via ``model_dump()`` so unknown
    provider fields survive intact for the SSE shaper downstream.

    Retry policy: the open-stream call is retried up to
    ``MAX_OPEN_STREAM_RETRIES`` times on ``ConnectError`` /
    ``TimeoutException`` (the same transient class
    :func:`_run_phyto_chat` treats as retriable). Once the iterator is
    returned, any mid-stream failure propagates immediately; retrying
    after partial delivery would silently lose chunks the client has
    already received, so the caller owns the resume decision.

    Args:
        user_query: The user's natural language query.
        obs_file_list: Optional list of OBS object keys to prepend as
            upload context (matches :func:`phyto_chat`).
        **kwargs: Same chat / OBS keyword options as :func:`phyto_chat`.

    Yields:
        OpenAI ``chat.completion.chunk`` payloads as plain ``dict``.

    Raises:
        McpError: When the open-stream call exhausts its retries on
            transient transport errors.
    """
    obs_file_list = [] if obs_file_list is None else list(obs_file_list)
    options = _chat_options(kwargs)
    if obs_file_list:
        user_query = await _query_with_upload_context(
            user_query, obs_file_list, options
        )
    messages = [
        {
            "role": "system",
            "content": get_prompt(
                options["prompt_file"], options["prompt_path"]
            ),
        },
        {"role": "user", "content": user_query},
    ]
    params = _build_stream_params(messages, options)
    api_key, base_url = _relay_llm_endpoint(
        options["api_key"], options["base_url"]
    )
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    stream_completions = await _open_chat_stream(client, params)
    async for chunk in stream_completions:
        yield chunk.model_dump()


def _build_stream_params(
    messages: list[dict[str, str]], options: dict[str, Any]
) -> dict[str, Any]:
    """Build the OpenAI ``chat.completions.create`` kwargs for streaming.

    Mirrors the parameter shape used by :func:`run_phyto_chat_cached`
    so streaming and non-streaming paths send identical sampling
    inputs to the provider; the only forced difference is
    ``stream=True``.
    """
    params: dict[str, Any] = {
        "messages": messages,
        "model": options["model"],
        "frequency_penalty": options["frequency_penalty"],
        "n": options["n"],
        "presence_penalty": options["presence_penalty"],
        "response_format": options["response_format"],
        "stream": True,
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


async def _open_chat_stream(
    client: AsyncOpenAI, params: dict[str, Any]
) -> Any:
    """Open one streaming chat completion with bounded transport retries.

    Retries up to ``MAX_OPEN_STREAM_RETRIES`` times on the transient
    transport class ``_run_phyto_chat`` already treats as retriable
    (``ConnectError`` / ``TimeoutException``). Non-transient failures
    (HTTPStatusError, malformed requests, etc.) propagate immediately
    so the API layer can map them to the correct HTTP status.
    """
    last_exc: BaseException | None = None
    for attempt in range(MAX_OPEN_STREAM_RETRIES + 1):
        try:
            return await client.chat.completions.create(**params)
        except (ConnectError, TimeoutException) as exc:
            last_exc = exc
            if attempt < MAX_OPEN_STREAM_RETRIES:
                await asyncio.sleep(1.5**attempt)
                continue
            logger.exception(
                "stream_phyto_chat_chunks: open-stream transport "
                "failure after %s retries",
                attempt,
            )
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Failed to open chat completion stream",
                )
            ) from exc
    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to open chat completion stream",
        )
    ) from last_exc


async def _stream_response_to_dict(stream_completions: Any) -> dict[str, Any]:
    """Collect streaming chunks into an OpenAI-style response dictionary.

    Accumulates both ``delta.content`` and ``delta.reasoning_content``
    so the rebuilt message keeps the reasoning trace returned by
    reasoner-style backends (DeepSeek-R1, Qwen-Reasoner, etc.); the
    field is read defensively with ``getattr`` to remain compatible
    with providers that never expose it. The last seen non-empty
    ``finish_reason`` survives so envelope clients can tell ``stop``
    from ``length`` / ``tool_calls`` / ``content_filter``.
    """
    full_content = ""
    full_reasoning = ""
    finish_reason: str | None = None
    chunk = None
    async for chunk in stream_completions:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
        delta_reasoning = getattr(delta, "reasoning_content", None)
        if delta_reasoning:
            full_reasoning += delta_reasoning
        chunk_finish = getattr(chunk.choices[0], "finish_reason", None)
        if chunk_finish:
            finish_reason = chunk_finish
    if chunk is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="No response received from model",
            )
        )
    chat_completions = chunk.model_dump()
    chat_completions.update(
        {
            "choices": [
                _stream_choice(full_content, full_reasoning, finish_reason)
            ]
        }
    )
    return chat_completions


def _stream_choice(
    full_content: str,
    full_reasoning: str,
    finish_reason: str | None,
) -> dict[str, Any]:
    """Return the assembled final streaming choice.

    The ``reasoning_content`` field is omitted when the backend never
    emitted any reasoning deltas so the rebuilt message stays compact
    for non-reasoner providers; reasoner backends get the accumulated
    trace placed beside ``content`` in OpenAI canonical position.
    """
    message: dict[str, Any] = {
        "content": full_content.strip(),
        "refusal": None,
        "role": "assistant",
        "annotations": None,
        "audio": None,
        "function_call": None,
        "tool_calls": [],
    }
    if full_reasoning:
        message["reasoning_content"] = full_reasoning.strip()
    return {
        "finish_reason": finish_reason or "stop",
        "index": 0,
        "logprobs": None,
        "message": message,
        "stop_reason": None,
    }
