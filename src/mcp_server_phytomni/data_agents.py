# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for interacting with a database using
natural language queries.

It includes functions to convert natural language to SQL, execute the query,
and to first rewrite the natural language query using a language model for
better performance.
"""
import asyncio
from random import uniform
from typing import Any, Dict, List, Optional, Union
from uuid import uuid1

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .chat_agents import phyto_chat
from .config.defaults import DataConfig
from .config.settings import SensitiveConfig
from .knowledge_agents import retrieve
from .utils import get_prompt, get_token

dc = DataConfig()
sc = SensitiveConfig().load()


async def nl2sql(message_content: str,
                 database_url: str = dc.DATABASE_URL,
                 workspace_id: str = dc.WORKSPACE_ID,
                 subject_id: str = dc.SUBJECT_ID,
                 dialog_id: str = dc.DIALOG_ID,
                 need_insight: bool = dc.NEED_INSIGHT,
                 simplify_response: bool = dc.SIMPLIFY_RESPONSE,
                 timeout: float = dc.TIMEOUT,
                 retriable_codes: List[int] = dc.RETRIABLE_CODES,
                 max_retries: int = dc.MAX_RETRIES,
                 ) -> Optional[List[Dict[str, Any]]]:
    """Convert a natural language query to SQL and execute it.

    This function sends a natural language message to a service that
    translates it into an SQL query, executes it against a specified
    database subject within a workspace, and returns the results.
    It supports conversational context via a dialog ID and includes
    retry mechanisms for transient errors.

    Args:
        message_content: The natural language query to be processed.
        database_url: The URL of the NL2SQL service.
        workspace_id: Identifier for the workspace containing the data.
        subject_id: Identifier for the specific database subject or schema
            to query against.
        dialog_id: Identifier for the current dialog or conversation session.
            If an empty string is provided, a new unique dialog ID
            will be generated.
        need_insight: Flag indicating whether to generate insights based on
            the query results.
        simplify_response: Flag indicating whether the structure of the
            response should be simplified.
        timeout: Total request timeout in seconds for each API call attempt.
        retriable_codes: List of HTTP status codes that will trigger a retry.
        max_retries: Maximum number of retry attempts for failed requests.

    Returns:
        A list of dictionaries representing the JSON response from the
        NL2SQL service.

    Raises:
        McpError: If the API call to the NL2SQL service fails after all
            retry attempts.
    """
    dialog_id = dialog_id if dialog_id else str(uuid1())
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    database_url,
                    headers={'X-Auth-Token': await get_token(),
                             'X-Workspace-Id': workspace_id,
                             'Content-Type': 'application/json'},
                    json={
                        'subject_id': subject_id,
                        'dialog_id': dialog_id if dialog_id else str(uuid1()),
                        'message_content': message_content,
                        'need_insight': need_insight,
                        'simplify_response': simplify_response,
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
                    message=f'Failed to query SQL database: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}'
                )) from e


async def rewrite_nl2sql(
    user_query: str,
    retrieve_url: str = dc.RETRIEVE_URL,
    data_repo_id: str = dc.DATA_REPO_ID,
    page_num: int = dc.PAGE_NUM,
    page_size: int = dc.DATA_PAGE_SIZE,
    filter_string: Optional[str] = dc.FILTER_STRING,
    scope: str = dc.SCOPE,
    rerank_url: str = dc.RERANK_URL,
    rerank_batch_size: int = dc.RERANK_BATCH_SIZE,
    score_threshold: float = dc.SCORE_THRESHOLD,
    prompt_file: str = dc.PROMPT_FILE,
    prompt_path: str = dc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = dc.FREQUENCY_PENALTY,
    n: int = dc.N,
    presence_penalty: float = dc.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = dc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = dc.RESPONSE_FORMAT,
    stream: bool = dc.STREAM,
    temperature: float = dc.TEMPERATURE,
    top_p: float = dc.TOP_P,
    user: str = dc.USER,
    database_url: str = dc.DATABASE_URL,
    workspace_id: str = dc.WORKSPACE_ID,
    subject_id: str = dc.SUBJECT_ID,
    dialog_id: str = dc.DIALOG_ID,
    need_insight: bool = dc.NEED_INSIGHT,
    simplify_response: bool = dc.SIMPLIFY_RESPONSE,
    timeout: float = dc.TIMEOUT,
    retriable_codes: List[int] = dc.RETRIABLE_CODES,
    max_retries: int = dc.MAX_RETRIES,
    max_tokens: int = dc.MAX_TOKENS,
) -> Optional[List[Dict[str, Any]]]:
    """Rewrite a natural language query and then execute it via NL2SQL.

    This function first performs RAG retrieval to enhance the query with
    relevant knowledge base documents, then processes the enhanced query
    through the `phyto_chat` service to rephrase or enhance it for better
    NL2SQL performance. The rewritten query is then passed to the `nl2sql`
    function to be converted into SQL and executed against a database.

    Args:
        user_query: The user's initial natural language query.
        retrieve_url: URL for the document retrieval service.
        data_repo_id: The ID of the primary knowledge repository to search for
            Data-Agent RAG functionality.
        page_num: Page number for paginated results from retrieval services.
        page_size: Number of items per page for paginated results from
            retrieval services.
        filter_string: Optional filter criteria string for metadata filtering
            during retrieval.
        scope: Scope of search for retrieval operations. 'both' searches
            documents and keywords, 'doc' searches only documents, 'keyword'
            searches only keywords.
        rerank_url: URL for the document reranking service.
        rerank_batch_size: Batch size for reranking operations, if reranking is
            applied to retrieved documents.
        score_threshold: Minimum relevance score threshold for retrieved items.
            Results below this threshold are typically discarded.
        prompt_file: Path to the prompt template file for query rewriting.
        prompt_path: Path or key within the prompt file for query rewriting.
        api_key: API key for the Phyto model.
        base_url: Base URL of the Phyto API service.
        model: Identifier of the Phyto model to use.
        frequency_penalty: Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        n: Number of completion choices to generate for each input.
        presence_penalty: Penalty applied to new tokens based on their presence
            in the text so far, discouraging repetition of concepts. Values
            range from -2.0 to 2.0.
        reasoning_effort: Specifies the level of reasoning effort for the
            language model.
        response_format: Desired response format from the language model.
            For example, `{'type': 'json_object'}` to request a JSON response.
        stream: Flag to enable or disable streaming of responses from the
            language model. If True, responses are sent as a series of events.
        temperature: Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        top_p: Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to `TOP_P`.
        user: User identifier for API interactions, particularly for chat or
            LLM services.
        database_url: URL for the database query service (e.g., NLQ).
        workspace_id: Identifier for the workspace containing the data.
        subject_id: Identifier for the specific database subject or schema.
        dialog_id: Conversation ID for multi-turn context.
        need_insight: Flag indicating whether to generate insights from
            database queries.
        simplify_response: Flag indicating whether to simplify the structure of
            database query responses.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.

    Returns:
        A list of dictionaries representing the JSON response from the
        NL2SQL service, or None if the request failed.

    Raises:
        McpError: If either the RAG retrieval, query rewriting, or the NL2SQL
            execution fails.
    """
    dialog_id = dialog_id if dialog_id else str(uuid1())
    retrieve_response = await retrieve(
        user_query=user_query,
        retrieve_url=retrieve_url,
        repo_id=data_repo_id,
        page_num=page_num,
        page_size=page_size,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=None,
        rerank_url=rerank_url,
        rerank_batch_size=rerank_batch_size,
        score_threshold=score_threshold,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    retrieve_results = []
    total_length = 0
    for i, doc in enumerate(retrieve_response.get('doc_list', [])):
        header = f"[scenario {i+1} begin] {doc['title']}"
        body = (f"{doc['subtitle']}\n{doc['content']}"
                if doc.get('subtitle') else doc.get('content', ''))
        fragment = f'{header}\n{body} [scenario {i+1} end]'
        if total_length + len(fragment) <= max_tokens:
            retrieve_results.append(fragment)
            total_length += len(fragment)
        else:
            break
    retrieve_context = '\n\n'.join(retrieve_results)
    user_query = get_prompt(prompt_file, 'user/database',
                            {'scenario_prompts': retrieve_context,
                             'user_query': user_query})
    phyto_response = await phyto_chat(
        user_query=user_query,
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
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
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )

    if (not phyto_response or 'choices' not in phyto_response or
            not phyto_response['choices']):
        raise McpError(ErrorData(
            code=INTERNAL_ERROR,
            message='Failed to get response from phyto_chat service'
        ))

    response = await nl2sql(
        message_content=phyto_response['choices'][0]['message']['content'],
        database_url=database_url,
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
