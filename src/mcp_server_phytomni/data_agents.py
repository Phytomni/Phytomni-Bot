# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import asyncio
from random import uniform
from typing import Any, Dict, List, Union
from uuid import uuid1

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .chat_agents import phyto_chat
from .config.defaults import DataConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt, get_token

dc = DataConfig()
sc = SensitiveConfig().load()


async def nl2sql(message_content: str,
                 workspace_id: str = dc.WORKSPACE_ID,
                 subject_id: str = dc.SUBJECT_ID,
                 dialog_id: str = dc.DIALOG_ID,
                 need_insight: bool = dc.NEED_INSIGHT,
                 simplify_response: bool = dc.SIMPLIFY_RESPONSE,
                 timeout: float = dc.TIMEOUT,
                 retriable_codes: List[int] = dc.RETRIABLE_CODES,
                 max_retries: int = dc.MAX_RETRIES,
                 ) -> List[Dict[str, Any]]:
    """Convert a natural language query to SQL and execute it.

    This function sends a natural language message to a service that
    translates it into an SQL query, executes it against a specified
    database subject within a workspace, and returns the results.
    It supports conversational context via a dialog ID and includes
    retry mechanisms for transient errors.

    Args:
        message_content: The natural language query to be processed.
        workspace_id: Identifier for the workspace containing the data.
            Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema
            to query against. Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session.
            If an empty string is provided (default), a new unique dialog ID
            will be generated.
        need_insight: Flag indicating whether to generate insights based on
            the query results. Defaults to `NEED_INSIGHT`.
        simplify_response: Flag indicating whether the structure of the
            response should be simplified. Defaults to `SIMPLIFY_RESPONSE`.
        timeout: Total request timeout in seconds for each API call attempt,
            including connection. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            attempt. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls that fail
            with a retriable status code or network error.
            Defaults to `MAX_RETRIES`.

    Returns:
        A list of dictionaries representing the JSON response from the
        NL2SQL service, typically containing the query results or related
        information. The exact structure depends on the service implementation.

    Raises:
        McpError: If the API call to the NL2SQL service fails after all
            retry attempts due to HTTP errors or network issues.
    """
    dialog_id = dialog_id if dialog_id else str(uuid1())
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url=dc.DATABASE_URL,
                    headers={"X-Auth-Token": await get_token(),
                             "X-Workspace-Id": workspace_id,
                             "Content-Type": "application/json"},
                    json={
                        "subject_id": subject_id,
                        "dialog_id": dialog_id if dialog_id else str(uuid1()),
                        "message_content": message_content,
                        "need_insight": need_insight,
                        "simplify_response": simplify_response,
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

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
                    message=f"Failed to query SQL database: {str(e)}")) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e


async def rewrite_nl2sql(
    user_query: str,
    prompt_file: str = dc.PROMPT_FILE,
    prompt_path: str = dc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = dc.FREQUENCY_PENALTY,
    max_tokens: int = dc.MAX_TOKENS,
    n: int = dc.N,
    presence_penalty: float = dc.PRESENCE_PENALTY,
    reasoning_effort: str = dc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = dc.RESPONSE_FORMAT,
    stream: bool = dc.STREAM,
    temperature: float = dc.TEMPERATURE,
    top_p: float = dc.TOP_P,
    user: str = dc.USER,
    workspace_id: str = dc.WORKSPACE_ID,
    subject_id: str = dc.SUBJECT_ID,
    dialog_id: str = dc.DIALOG_ID,
    need_insight: bool = dc.NEED_INSIGHT,
    simplify_response: bool = dc.SIMPLIFY_RESPONSE,
    timeout: float = dc.TIMEOUT,
    retriable_codes: List[int] = dc.RETRIABLE_CODES,
    max_retries: int = dc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Rewrite a natural language query using a language model and then
        execute it via NL2SQL.

    This function first processes the `user_query` through the `phyto_chat`
    service to potentially rephrase or enhance it for better NL2SQL
    performance. The rewritten query is then passed to the `nl2sql` function
    to be converted into SQL and executed against a database.

    Args:
        user_query: The user's initial natural language query or prompt.
            This query is first rewritten by a language model.
        prompt_file: Path to the prompt template file used for constructing
            the prompt for the query rewriting step. Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for query rewriting.
            Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for query
            rewriting. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for query rewriting.
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for query rewriting.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in the
            query rewriting step. Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the rewritten
            query. Defaults to `MAX_TOKENS`.
        n: Number of rewritten query choices to generate by the Phyto model.
            Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in the query
            rewriting step. Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during query rewriting. Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for the Phyto
            model during query rewriting. Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for the query rewriting
            step. Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for the query rewriting
            step. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for the query rewriting
            step. Defaults to `TOP_P`.
        user: Unique session identifier for the end-user, passed to the
            Phyto model. Defaults to `USER`.
        workspace_id: Identifier for the workspace containing the data, passed
            to the `nl2sql` function. Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
            Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function. If an empty string is provided
            (default), a new unique dialog ID will be generated by this
            function before calling `nl2sql`.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
            Defaults to `NEED_INSIGHT`.
        simplify_response: Flag indicating whether the structure of the
            response from the `nl2sql` function should be simplified.
            Defaults to `SIMPLIFY_RESPONSE`.
        timeout: Total request timeout in seconds for each underlying API call
            (both Phyto rewriting and NL2SQL execution). Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying API calls.
            Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary representing the JSON response from the `nl2sql` service,
        obtained after executing the rewritten query. The exact structure
        depends on the `nl2sql` service implementation and the
        `simplify_response` flag.

    Raises:
        McpError: If either the `phyto_chat` query rewriting step or the
            subsequent `nl2sql` execution fails after all retry attempts.
    """
    dialog_id = dialog_id if dialog_id else str(uuid1())
    user_query = get_prompt(prompt_file, 'user/database',
                            {'user_query': user_query})
    phyto_response = await phyto_chat(
        user_query=user_query,
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        max_tokens=max_tokens,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await nl2sql(
        message_content=phyto_response['choices'][0]['message']['content'],
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=simplify_response,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    return response
