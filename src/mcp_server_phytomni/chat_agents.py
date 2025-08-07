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
    reasoning_effort: Optional[str] = cc.REASONING_EFFORT,
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

    async def make_phyto_chat() -> Optional[Dict[str, Any]]:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        for attempt in range(max_retries + 1):
            try:
                common_params = {
                    'messages': messages,
                    'model': model,
                    'frequency_penalty': frequency_penalty,
                    'n': n,
                    'presence_penalty': presence_penalty,
                    'response_format': response_format,
                    'stream': stream,
                    'temperature': temperature,
                    'top_p': top_p,
                    'user': user,
                    'timeout': timeout,
                }

                if 'reasoner' in model and reasoning_effort is not None:
                    common_params['reasoning_effort'] = reasoning_effort

                if stream:
                    stream_completions = await client.chat.completions.create(
                        **common_params)
                    full_content = ''
                    chunk = None
                    async for chunk in stream_completions:
                        if chunk.choices and chunk.choices[0].delta.content:
                            content_piece = chunk.choices[0].delta.content
                            full_content += content_piece
                    if chunk is None:
                        raise McpError(ErrorData(
                            code=INTERNAL_ERROR,
                            message='No response received from model',
                        ))
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

                chat_completions = await client.chat.completions.create(
                    **common_params)
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
