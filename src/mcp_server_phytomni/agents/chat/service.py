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
import inspect
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any, NamedTuple, NotRequired, TypedDict, Unpack

from httpx import ConnectError, HTTPStatusError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData
from openai import APIConnectionError

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
from ...config.relay_mode import (
    RELAY_TIMEOUT_PROFILE_HEADER,
    relay_mode_enabled,
)
from ...config.settings import get_sensitive_config
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...runtime.langgraph_runner import ainvoke_graph
from ...runtime.locale import (
    SupportedLocale,
    current_effective_locale,
    locale_instruction,
)
from ...runtime.outbound import OutboundPoolName, current_outbound_runtime
from ...storage.downloads import download_list_convert
from ..shared.conversation_messages import (
    build_model_messages,
    normalize_conversation_messages,
)
from ..shared.options import reject_chat_provider_overrides
from .completion_validation import (
    InvalidChatCompletionError,
    is_cacheable_chat_completion,
    require_successful_chat_completion,
)

logger = logging.getLogger(__name__)

CHAT_CONFIG = ChatConfig()

# Retry budget for opening one streaming chat-completion connection.
# Once ``client.chat.completions.create(stream=True, ...)`` returns
# the iterator, any mid-stream failure propagates immediately — a
# silent retry would lose already-emitted chunks and corrupt the SSE
# timeline from the client's point of view.
MAX_OPEN_STREAM_RETRIES = 1


class _ChatCacheKey(NamedTuple):
    """Semantic fields that identify one cached chat completion."""

    messages: list[dict[str, str]]
    response_format: dict[str, Any]
    locale: SupportedLocale


class _ChatCacheRequest(NamedTuple):
    """Provider and transport settings for one cache miss."""

    model: str
    temperature: float
    top_p: float
    frequency_penalty: float
    presence_penalty: float
    n: int
    max_tokens: int | None
    reasoning_effort: str | None
    user: str
    timeout: float
    stream: bool
    relay_timeout_profile: str | None


class ChatCacheCall(TypedDict):
    """Keyword-compatible public call shape for the chat cache adapter."""

    messages: list[dict[str, str]]
    model: str
    temperature: float
    top_p: float
    frequency_penalty: float
    presence_penalty: float
    n: int
    max_tokens: int | None
    response_format: dict[str, Any]
    reasoning_effort: str | None
    user: str
    timeout: float
    stream: bool
    locale: NotRequired[SupportedLocale]
    relay_timeout_profile: NotRequired[str | None]


async def phyto_chat_with_follow(
    user_query: str,
    obs_file_list: list[str] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    *,
    locale: SupportedLocale | None = None,
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
    effective_locale = locale or current_effective_locale()
    prompt_file = kwargs.get("prompt_file", CHAT_CONFIG.PROMPT_FILE)
    graph_thread_id = kwargs.get("thread_id")
    base_kwargs = {
        key: value for key, value in kwargs.items() if key != "thread_id"
    }

    phyto_response = await phyto_chat(
        user_query=user_query,
        obs_file_list=obs_file_list,
        semaphore=semaphore,
        locale=effective_locale,
        thread_id=graph_thread_id,
        **base_kwargs,
    )

    system_response_content = message_content(phyto_response)

    follow_kwargs = {
        **base_kwargs,
        "prompt_file": prompt_file,
        "locale": effective_locale,
    }
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
    *,
    locale: SupportedLocale | None = None,
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
    effective_locale = locale or current_effective_locale()
    conversation_messages = normalize_conversation_messages(
        kwargs.get("conversation_messages")
    )
    graph_thread_id = kwargs.get("thread_id")
    graph_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key not in {"conversation_messages", "thread_id"}
    }
    chat_kwargs: dict[str, Any] = {
        **graph_kwargs,
        "with_follow_up": False,
        "locale": effective_locale,
    }
    if semaphore is not None:
        chat_kwargs["semaphore"] = semaphore
    initial_state: dict[str, Any] = {
        "user_query": user_query,
        "obs_file_list": list(obs_file_list) if obs_file_list else [],
        "chat_kwargs": chat_kwargs,
        "locale": effective_locale,
    }
    if conversation_messages:
        initial_state["conversation_messages"] = conversation_messages
    final_state = await ainvoke_graph(
        _cached_chat_app(),
        initial_state,
        thread_id=graph_thread_id,
    )
    return final_state.get("response")


@lru_cache(maxsize=1)
def _cached_chat_app() -> Any:
    """Lazy singleton of the compiled chat subgraph.

    Resolves ``builder`` dynamically via ``importlib.import_module``
    rather than ``from .builder import build_chat_graph``: a
    top-level from-import would close the service → builder → graph
    → service cycle at parse time (graph.py imports this module for
    helpers and late-bound ``phyto_chat`` / ``get_prompt`` lookups).
    The import is dynamic: it fires once per process at first call,
    after every module above has finished loading. ``lru_cache``
    makes the compile happen at most once and gives tests a
    ``cache_clear()`` hook.
    """
    builder_module = importlib.import_module(".builder", package=__package__)
    return builder_module.build_chat_graph()


def _chat_options(values: dict[str, Any]) -> dict[str, Any]:
    """Resolve keyword-compatible chat and OBS options."""
    reject_chat_provider_overrides(values)
    sensitive = get_sensitive_config()
    default_access_key_id, default_secret_access_key = (
        sensitive.obs_credentials()
    )
    response_format = values.get("response_format")
    retriable_codes = values.get("retriable_codes")
    effective_locale = values.get("locale") or current_effective_locale()
    return {
        "prompt_file": values.get("prompt_file", CHAT_CONFIG.PROMPT_FILE),
        "prompt_path": values.get("prompt_path", CHAT_CONFIG.PROMPT_PATH),
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
        "relay_timeout_profile": values.get("relay_timeout_profile"),
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
        "locale": effective_locale,
        "locale_instruction": locale_instruction(effective_locale),
    }


def _apply_locale_instruction(
    system_prompt: str, options: dict[str, Any]
) -> str:
    """Prepend the request locale instruction to a provider system prompt."""
    instruction = options.get("locale_instruction")
    if not isinstance(instruction, str) or not instruction:
        return system_prompt
    return f"{instruction}\n\n{system_prompt}"


async def _query_with_upload_context(
    user_query: str,
    obs_file_list: list[str],
    options: dict[str, Any],
) -> str:
    """Download uploaded files and prepend bounded context to the query."""
    upload_str_list = await download_list_convert(
        obs_file_list=obs_file_list,
        server_dir=options["server_dir"],
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


def _relay_timeout_headers(profile: str | None) -> dict[str, str] | None:
    """Return the child-only timeout profile header in relay mode."""
    if not relay_mode_enabled() or profile is None:
        return None
    return {RELAY_TIMEOUT_PROFILE_HEADER: profile}


@func_cache(
    key_params=["cache_key"],
    ttl=LONG_TTL_SECONDS,
    cache_if=is_cacheable_chat_completion,
)
async def _run_chat_completion_cached(
    cache_key: _ChatCacheKey,
    request: _ChatCacheRequest,
) -> dict[str, Any]:
    """Issue one LLM completion and cache the normalized dict.

    ``cache_key`` contains the two historical semantic fields used by the
    cache (messages and response format). The remaining settings are carried
    in ``request`` so rotating credentials, endpoints, or transport flags
    cannot change cache identity.

    HTTP and transport failures propagate as exceptions. Structurally invalid
    normal values are rejected after normalization, while the cache admission
    predicate also lazily rejects historical values under the same policy.
    """
    runtime = current_outbound_runtime()
    params: dict[str, Any] = {
        "messages": cache_key.messages,
        "model": request.model,
        "frequency_penalty": request.frequency_penalty,
        "n": request.n,
        "presence_penalty": request.presence_penalty,
        "response_format": cache_key.response_format,
        "stream": request.stream,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "user": request.user,
        "timeout": request.timeout,
    }
    if request.max_tokens is not None:
        params["max_tokens"] = request.max_tokens
    if "reasoner" in request.model and request.reasoning_effort is not None:
        params["reasoning_effort"] = request.reasoning_effort
    if extra_headers := _relay_timeout_headers(request.relay_timeout_profile):
        params["extra_headers"] = extra_headers
    async with runtime.pools.lease(OutboundPoolName.LLM):
        chat_completions = await runtime.openai.chat.completions.create(
            **params
        )
        if request.stream:
            payload = await _stream_response_to_dict(chat_completions)
        else:
            payload = chat_completions.model_dump()
    normalized = normalize_chat_completion_dict(payload)
    return require_successful_chat_completion(normalized)


async def run_phyto_chat_cached(
    **call: Unpack[ChatCacheCall],
) -> dict[str, Any]:
    """Call the typed chat cache seam with its stable keyword surface.

    The adapter keeps existing callers readable while the decorated cache
    primitive receives explicit semantic and infrastructure records. That
    separation makes the cache policy visible in types instead of relying on
    a long positional signature and a linter waiver.
    """
    reject_chat_provider_overrides(call)
    cache_key = _ChatCacheKey(
        messages=call["messages"],
        response_format=call["response_format"],
        locale=call.get("locale") or current_effective_locale(),
    )
    request = _ChatCacheRequest(
        model=call["model"],
        temperature=call["temperature"],
        top_p=call["top_p"],
        frequency_penalty=call["frequency_penalty"],
        presence_penalty=call["presence_penalty"],
        n=call["n"],
        max_tokens=call["max_tokens"],
        reasoning_effort=call["reasoning_effort"],
        user=call["user"],
        timeout=call["timeout"],
        stream=call["stream"],
        relay_timeout_profile=call.get("relay_timeout_profile"),
    )
    return await _run_chat_completion_cached(
        cache_key=cache_key,
        request=request,
    )


def clear_chat_cache() -> None:
    """Drop cached chat completions from the local SQLite store."""
    _run_chat_completion_cached.cache_clear()


async def _run_phyto_chat(
    messages: list[dict[str, str]],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Call the Phyto chat endpoint with retry handling.

    Thin dispatcher around ``run_phyto_chat_cached`` that owns the
    retry loop. The cached inner handles a single attempt and rejects
    transport, HTTP, and structurally invalid provider results. Both retry
    helpers (``retry_http_status_or_raise`` / ``retry_network_or_raise``)
    either return True (sleep + retry) or raise ``McpError`` when retries are
    exhausted or the failure is non-retriable.
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
                user=options["user"],
                timeout=options["timeout"],
                stream=options["stream"],
                locale=options["locale"],
                relay_timeout_profile=options.get("relay_timeout_profile"),
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
        except (ConnectError, TimeoutException, APIConnectionError) as exc:
            if await retry_network_or_raise(
                exc,
                attempt=attempt,
                max_retries=options["max_retries"],
            ):
                continue
        except InvalidChatCompletionError as exc:
            if await retry_network_or_raise(
                exc,
                attempt=attempt,
                max_retries=options["max_retries"],
                message="Failed to generate from Phyto",
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
    *,
    locale: SupportedLocale | None = None,
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Yield raw provider chunks for one streaming chat completion.

    Mirrors :func:`phyto_chat` for prompt construction and OBS upload
    context but deliberately bypasses :func:`run_phyto_chat_cached`:
    that primitive accepts ``stream: bool`` and buffers the full
    response into ``func_cache`` once collected, which is incompatible
    with a real token stream. This function calls
    the shared OpenAI client's ``chat.completions.create(stream=True, ...)``
    directly and yields each chunk via ``model_dump()`` so unknown
    provider fields survive intact for the SSE shaper downstream.

    Retry policy: the open-stream call is retried up to
    ``MAX_OPEN_STREAM_RETRIES`` times on raw HTTP transport errors or
    their OpenAI SDK ``APIConnectionError`` wrapper. This is the same
    transient class :func:`_run_phyto_chat` treats as retriable. Once the
    iterator is returned, any mid-stream failure propagates immediately;
    retrying after partial delivery would silently lose chunks the client has
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
    option_values = dict(kwargs)
    if locale is not None:
        option_values["locale"] = locale
    options = _chat_options(option_values)
    if obs_file_list:
        user_query = await _query_with_upload_context(
            user_query, obs_file_list, options
        )
    system_prompt = get_prompt(options["prompt_file"], options["prompt_path"])
    messages = build_model_messages(
        system_prompt=_apply_locale_instruction(system_prompt, options),
        user_query=user_query,
        conversation_messages=kwargs.get("conversation_messages"),
    )
    params = _build_stream_params(messages, options)
    runtime = current_outbound_runtime()
    for attempt in range(MAX_OPEN_STREAM_RETRIES + 1):
        open_error: BaseException | None = None
        async with runtime.pools.lease(OutboundPoolName.LLM):
            try:
                stream_completions = (
                    await runtime.openai.chat.completions.create(**params)
                )
            except (ConnectError, TimeoutException, APIConnectionError) as exc:
                open_error = exc
            else:
                try:
                    async for chunk in stream_completions:
                        yield chunk.model_dump()
                finally:
                    await _close_async_stream(stream_completions)
                return
        if open_error is None:
            raise RuntimeError("stream open did not produce a result")
        if attempt < MAX_OPEN_STREAM_RETRIES:
            await asyncio.sleep(1.5**attempt)
            continue
        logger.warning(
            "stream_phyto_chat_chunks: open-stream transport failure "
            "after %s retries",
            attempt,
        )
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Failed to open chat completion stream",
            )
        ) from open_error


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
    if extra_headers := _relay_timeout_headers(
        options.get("relay_timeout_profile")
    ):
        params["extra_headers"] = extra_headers
    return params


async def _close_async_stream(stream: Any) -> None:
    """Close a provider stream when its consumer reaches any terminal path."""
    close = getattr(stream, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


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
    try:
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
    finally:
        await _close_async_stream(stream_completions)
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
