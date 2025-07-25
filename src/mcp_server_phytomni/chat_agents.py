# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides a client for interacting with Phyto language models.

It includes a function to generate text based on a user query and a system
prompt, with support for various model parameters and retry mechanisms.
"""
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
from .utils import download_list_convert, get_prompt

cc = ChatConfig()
sc = SensitiveConfig().load()


async def phyto_chat(
    user_query: str,
    obs_file_list: List[str] = [],
    prompt_file: str = cc.PROMPT_FILE,
    prompt_path: str = cc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = cc.FREQUENCY_PENALTY,
    n: int = cc.N,
    presence_penalty: float = cc.PRESENCE_PENALTY,
    reasoning_effort: str = cc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = cc.RESPONSE_FORMAT,
    stream: bool = cc.STREAM,
    temperature: float = cc.TEMPERATURE,
    top_p: float = cc.TOP_P,
    user: str = cc.USER,
    server_dir: str = cc.TEMP_DIR,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = cc.OBS_SERVER,
    bucket_name: str = cc.BUCKET_NAME,
    part_size: int = cc.PART_SIZT,
    task_num: int = cc.TASK_NUM,
    timeout: float = cc.TIMEOUT,
    retriable_codes: List[int] = cc.RETRIABLE_CODES,
    max_retries: int = cc.MAX_RETRIES,
    max_concurrency: int = cc.MAX_CONCURRENCY,
    max_workers: int = cc.MAX_WORKERS,
    max_tokens: int = cc.MAX_TOKENS,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Generate text using a Phyto model.

    This function sends a request to a Phyto language model and returns the
    generated text. It supports various model parameters and includes a retry
    mechanism with exponential backoff for transient errors.

    Args:
        user_query: The user's natural language query.
        prompt_file: The path to the prompt template file.
        prompt_path: The path to the specific prompt within the template file.
        api_key: The API key for the Phyto model.
        base_url: The base URL for the Phyto API service.
        model: The ID of the model to use.
        frequency_penalty: The frequency penalty for the model.
        n: The number of chat completion choices to generate.
        presence_penalty: The presence penalty for the model.
        reasoning_effort: The reasoning effort for the model.
        response_format: The desired response format from the model.
        stream: Whether to stream the response from the model.
        temperature: The temperature for the model.
        top_p: The top_p for the model.
        user: The user ID for the model.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.
        semaphore: An optional semaphore to limit concurrency.

    Returns:
        A dictionary containing the API response from the Phyto model.

    Raises:
        McpError: If the API call fails after all retry attempts.
    """
    if obs_file_list:
        upload_str_list = await download_list_convert(
            obs_file_list=obs_file_list,
            server_dir=server_dir,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
            part_size=part_size,
            task_num=task_num,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            max_workers=max_workers,
        )
        upload_results = []
        total_length = 0
        for i, doc in enumerate(upload_str_list):
            fragment = (f'[user upload file {i+1} begin]\n'
                        f'{doc}\n[user upload file {i+1} end]')
            if total_length + len(fragment) <= max_tokens:
                upload_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        upload_context = '\n\n'.join(upload_results)
        user_query = (
            'Based on the following files uploaded by the user:\n'
            f'{upload_context}\n'
            f"Please answer the user's questions:\n{user_query}")
    messages = [
        {
            'role': 'system',
            'content': get_prompt(prompt_file, prompt_path),
        },
        {
            'role': 'user',
            'content': user_query,
        },
    ]
    if 'reasoner' not in model:
        reasoning_effort = None

    async def make_phyto_chat() -> Dict[str, Any]:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        for attempt in range(max_retries + 1):
            try:
                if stream:
                    stream_completions = await client.chat.completions.create(
                        messages=messages,
                        model=model,
                        frequency_penalty=frequency_penalty,
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
                        'stop_reason': None,
                    }]})
                    return chat_completions
                else:
                    chat_completions = await client.chat.completions.create(
                        messages=messages,
                        model=model,
                        frequency_penalty=frequency_penalty,
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
                    message=f'Failed to generate from Phyto: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e

    if semaphore is not None:
        async with semaphore:
            return await make_phyto_chat()
    else:
        return await make_phyto_chat()
