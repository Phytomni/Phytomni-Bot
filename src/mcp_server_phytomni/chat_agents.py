# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import asyncio
from random import uniform
from typing import Any, Dict, List, Optional, Union

from httpx import ConnectError, HTTPStatusError
from httpx import TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from openai import AsyncOpenAI

from .config.defaults import ChatConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt

cc = ChatConfig()
sc = SensitiveConfig().load()


async def phyto_chat(
    user_query: str,
    prompt_file: str = cc.PROMPT_FILE,
    prompt_path: str = cc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = cc.FREQUENCY_PENALTY,
    max_tokens: int = cc.MAX_TOKENS,
    n: int = cc.N,
    presence_penalty: float = cc.PRESENCE_PENALTY,
    reasoning_effort: str = cc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = cc.RESPONSE_FORMAT,
    stream: bool = cc.STREAM,
    temperature: float = cc.TEMPERATURE,
    top_p: float = cc.TOP_P,
    user: str = cc.USER,
    timeout: float = cc.TIMEOUT,
    retriable_codes: List[int] = cc.RETRIABLE_CODES,
    max_retries: int = cc.MAX_RETRIES,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Generate text using a Phyto model, adapting to different API versions.

    This function constructs a request to a Phyto language model.
    It includes logic for dynamic adjustment of max_tokens
    and implements retry mechanisms with exponential backoff for transient
    errors.

    Args:
        user_query: The user's natural language query or prompt.
        prompt_file: Path to the prompt template file containing system
            prompts. Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt. Defaults to `PROMPT_PATH`.
        api_key: API key for authentication, primarily for Phyto models.
            Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service.
            Defaults to `BASE_URL`.
        model: Identifier of the model to use. This determines
            which internal API call structure is used. Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0).
            Positive values discourage repeating tokens.
            Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the completion.
            If 0 or None, it's dynamically calculated based on the input
            length to fit within model limits. For models, this corresponds
            to `max_completion_tokens`. Defaults to `MAX_TOKENS`.
        n: Number of chat completion choices to generate.
            Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0). Positive
            values encourage introducing new concepts.
            Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible
            Phyto models (e.g., "auto", "high").
            Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for Phyto
            models, e.g., `{"type": "json_object"}` for JSON mode.
            Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output.
            Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0). Lower values (e.g., 0.2)
            make output more deterministic, higher values (e.g., 0.8) make it
            more random. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0). The model considers
            tokens with `top_p` probability mass. E.g., 0.1 means only
            tokens comprising the top 10% probability mass are considered.
            Mutually exclusive with temperature in some models.
            Defaults to `TOP_P`.
        user: Unique session identifier for the end-user (1-64 characters).
            Defaults to `USER`.
        timeout: Total request timeout in seconds for each API call attempt,
            including connection. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            attempt. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls that fail
            with a retriable status code or network error.
            Defaults to `MAX_RETRIES`.
        semaphore: An optional asyncio.Semaphore to limit the number of
            concurrent calls to the Phyto API. Defaults to `None`.

    Returns:
        A dictionary containing the API response from the Phyto model.
        The structure of this dictionary depends on the model and API version
        used.

    Raises:
        McpError: If the API call fails after all retry attempts due to
            HTTP errors, network issues, or if an unhandled error occurs.
    """
    messages = [
        {
            "role": "system",
            "content": get_prompt(prompt_file, prompt_path),
        },
        {
            "role": "user",
            "content": user_query,
        },
    ]
    if 'reasoner' not in model:
        reasoning_effort = None
    if not max_tokens:
        max_tokens = cc.MAX_TOKENS - int(
            (len(messages[0]['content']) + len(user_query)) / 5)

    async def make_phyto_chat() -> Dict[str, Any]:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        for attempt in range(max_retries + 1):
            try:
                if stream:
                    stream_completions = await client.chat.completions.create(
                        messages=messages,
                        model=model,
                        frequency_penalty=frequency_penalty,
                        # max_completion_tokens=max_tokens,
                        n=n,
                        presence_penalty=presence_penalty,
                        reasoning_effort=reasoning_effort,
                        response_format=response_format,
                        stream=stream,
                        temperature=temperature,
                        top_p=top_p,
                        user=user,
                        timeout=timeout,
                    )
                    full_content = ''
                    async for chunk in stream_completions:
                        if chunk.choices and chunk.choices[0].delta.content:
                            content_piece = chunk.choices[0].delta.content
                            full_content += content_piece
                    chat_completions = chunk.model_dump()
                    chat_completions.update({'choices': [{
                        'finish_reason': 'stop',
                        'index': 0,
                        'logprobs': None,
                        'message': {
                            'content': full_content.strip(),
                            'refusal': None,
                            'role': 'assistant',
                            'annotations': None,
                            'audio': None,
                            'function_call': None,
                            'tool_calls': []},
                        'stop_reason': None
                    }]})
                    return chat_completions
                else:
                    chat_completions = await client.chat.completions.create(
                        messages=messages,
                        model=model,
                        frequency_penalty=frequency_penalty,
                        # max_completion_tokens=max_tokens,
                        n=n,
                        presence_penalty=presence_penalty,
                        reasoning_effort=reasoning_effort,
                        response_format=response_format,
                        stream=stream,
                        temperature=temperature,
                        top_p=top_p,
                        user=user,
                        timeout=timeout,
                    )
                    return chat_completions.model_dump()

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to generate from Phyto: {str(e)}"
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e

    if semaphore is not None:
        async with semaphore:
            return await make_phyto_chat()
    else:
        return await make_phyto_chat()
