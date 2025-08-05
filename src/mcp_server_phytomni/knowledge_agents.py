# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for interacting with a knowledge base.

It includes functions for retrieving, reranking, and generating text based on
the retrieved knowledge.
"""
import asyncio
from random import uniform
from typing import List, Dict, Any, Optional, Union

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .chat_agents import phyto_chat
from .config.defaults import KnowledgeConfig
from .config.settings import SensitiveConfig
from .utils import download_list_convert, get_prompt, split_list

kc = KnowledgeConfig()
sc = SensitiveConfig().load()


async def retrieve(user_query: str,
                   retrieve_url: str = kc.RETRIEVE_URL,
                   repo_id: str = kc.REPO_ID,
                   page_num: int = kc.PAGE_NUM,
                   page_size: int = kc.PAGE_SIZE,
                   filter_string: Optional[str] = kc.FILTER_STRING,
                   scope: str = kc.SCOPE,
                   extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
                   rerank_url: str = kc.RERANK_URL,
                   rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
                   score_threshold: float = kc.SCORE_THRESHOLD,
                   timeout: float = kc.TIMEOUT,
                   retriable_codes: List[int] = kc.RETRIABLE_CODES,
                   max_retries: int = kc.MAX_RETRIES,
                   ) -> Dict[str, Any]:
    """Retrieve and rerank documents from a knowledge base.

    This function queries a knowledge base service, retrieves documents based
    on the user query, and then reranks them to improve relevance. It supports
    searching within document content, keywords, or both. The function also
    includes a retry mechanism for transient network or server errors.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id: The ID of the primary knowledge repository to search.
        page_num: The page number for pagination of retrieval results.
        page_size: The number of documents to retrieve per page. This also
                   serves as the `top_n` parameter for the reranking process.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A dictionary containing the reranked list of documents and a total
        count. The dictionary has 'doc_list' and 'total' keys.

    Raises:
        McpError: If the API call to the retrieval or reranking service fails
                  after all retries.
        ValueError: If an unsupported `scope` value is provided.
    """
    async def make_retrieve_request(client, scope):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    retrieve_url,
                    headers={'Content-Type': 'application/json'},
                    json={'repo_id': repo_id,
                          'content': user_query,
                          'page_num': page_num,
                          'page_size': page_size,
                          'filter_string': filter_string,
                          'scope': scope,
                          'extra_repo_ids': extra_repo_ids},
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()['doc_list']

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
                    message=f'Failed to retrieve knowledge base: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e

    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        if scope in ('doc', 'keyword'):
            doc_list = await make_retrieve_request(client, scope)
        elif scope == 'both':
            tasks = [make_retrieve_request(client, scope)
                     for scope in ['doc', 'keyword']]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            doc_list = [doc for each_result in results for doc in each_result]
        else:
            raise ValueError("Invalid scope value. Must be 'doc', 'keyword',"
                             " or 'both'.")
    return {'doc_list': await rerank(
                user_query=user_query,
                doc_list=doc_list,
                rerank_url=rerank_url,
                top_n=page_size,
                rerank_batch_size=rerank_batch_size,
                score_threshold=score_threshold,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            ),
            'total': 10000}


async def multi_retrieve(
    user_query: str,
    retrieve_url: str = kc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = kc.REPO_ID_DICT,
    page_num: int = kc.PAGE_NUM,
    filter_string: Optional[str] = kc.FILTER_STRING,
    scope: str = kc.SCOPE,
    extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
    rerank_url: str = kc.RERANK_URL,
    rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
    score_threshold: float = kc.SCORE_THRESHOLD,
    top_n: int = kc.TOP_N,
    timeout: float = kc.TIMEOUT,
    retriable_codes: List[int] = kc.RETRIABLE_CODES,
    max_retries: int = kc.MAX_RETRIES,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Concurrently retrieve and rerank documents from multiple repositories.

    This function calls the `retrieve` function for each repository specified
    in `repo_id_dict`. It then merges the results, sorts them by relevance
    score, and returns the top N documents. A semaphore can be used to limit
    the concurrency of the retrieval operations.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id_dict: A dictionary mapping repository IDs to their page sizes.
        page_num: The page number for pagination of retrieval results.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        top_n: The total number of top-scoring documents to return.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.
        semaphore: An optional semaphore to limit concurrency.

    Returns:
        A dictionary containing the merged and sorted list of documents and a
        total count. The dictionary has 'doc_list' and 'total' keys.

    Raises:
        McpError: If any of the underlying `retrieve` operations fail.
    """
    if not repo_id_dict:
        repo_id_dict = kc.REPO_ID_DICT

    async def make_multi_retrieve():
        try:
            tasks = [
                retrieve(
                    user_query=user_query,
                    retrieve_url=retrieve_url,
                    repo_id=repo_id,
                    page_num=page_num,
                    page_size=page_size,
                    filter_string=filter_string,
                    scope=scope,
                    extra_repo_ids=extra_repo_ids,
                    rerank_url=rerank_url,
                    rerank_batch_size=rerank_batch_size,
                    score_threshold=score_threshold,
                    timeout=timeout,
                    retriable_codes=retriable_codes,
                    max_retries=max_retries,
                )
                for repo_id, page_size in repo_id_dict.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            merged_docs = []
            for result in results:
                if isinstance(result, dict) and 'doc_list' in result:
                    merged_docs.extend(result['doc_list'])
            sorted_docs = sorted(merged_docs,
                                 key=lambda x: x['score'],
                                 reverse=True)
            if top_n is not None and top_n > 0:
                sorted_docs = sorted_docs[:top_n]
            return {
                'doc_list': sorted_docs,
                'total': 10000,
            }
        except Exception as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f'Multi-retrieve operation failed: {str(e)}',
            )) from e

    if semaphore is not None:
        async with semaphore:
            return await make_multi_retrieve()
    else:
        return await make_multi_retrieve()


async def multi_retrieve_generate(
    user_query: str,
    retrieve_url: str = kc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = kc.REPO_ID_DICT,
    page_num: int = kc.PAGE_NUM,
    filter_string: Optional[str] = kc.FILTER_STRING,
    scope: str = kc.SCOPE,
    extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
    rerank_url: str = kc.RERANK_URL,
    rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
    score_threshold: float = kc.SCORE_THRESHOLD,
    top_n: int = kc.TOP_N,
    prompt_file: str = kc.PROMPT_FILE,
    prompt_path: str = kc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = kc.FREQUENCY_PENALTY,
    max_tokens: int = kc.MAX_TOKENS,
    n: int = kc.N,
    presence_penalty: float = kc.PRESENCE_PENALTY,
    reasoning_effort: str = kc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = kc.RESPONSE_FORMAT,
    stream: bool = kc.STREAM,
    temperature: float = kc.TEMPERATURE,
    top_p: float = kc.TOP_P,
    user: str = kc.USER,
    obs_file_list: List[str] = [],
    server_dir: str = kc.TEMP_DIR,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = kc.OBS_SERVER,
    bucket_name: str = kc.BUCKET_NAME,
    part_size: int = kc.PART_SIZT,
    task_num: int = kc.TASK_NUM,
    max_concurrency: int = kc.MAX_CONCURRENCY,
    max_workers: int = kc.MAX_WORKERS,
    timeout: float = kc.TIMEOUT,
    retriable_codes: List[int] = kc.RETRIABLE_CODES,
    max_retries: int = kc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Perform retrieval-augmented generation (RAG) with optional file context.

    This function first retrieves relevant documents using `multi_retrieve`,
    then uses the retrieved documents to augment a prompt for a language
    model to generate a response. Optionally processes user-uploaded files
    from OBS to provide additional context for the query.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id_dict: A dictionary mapping repository IDs to their page sizes.
        page_num: The page number for pagination of retrieval results.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        top_n: The total number of top-scoring documents to use for generation.
        prompt_file: The path to the prompt template file.
        prompt_path: The path to the specific prompt within the template file.
        api_key: The API key for the language model.
        base_url: The base URL for the language model service.
        model: The ID of the language model to use.
        frequency_penalty: The frequency penalty for the language model.
        max_tokens: The maximum number of tokens to generate.
        n: The number of chat completion choices to generate.
        presence_penalty: The presence penalty for the language model.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired response format from the language model.
        stream: Whether to stream the response from the language model.
        temperature: The temperature for the language model.
        top_p: The top_p for the language model.
        user: The user ID for the language model.
        obs_file_list: List of OBS object keys (file paths) to download and
            include as context in the query. Files are converted to markdown.
        server_dir: Local directory path for temporary file storage during
            file downloads and processing.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket containing the files.
        part_size: Size of each part for multipart downloads from OBS.
        task_num: Number of concurrent tasks for multipart downloads from OBS.
        max_concurrency: Maximum number of files to download from OBS
            concurrently.
        max_workers: Maximum number of worker processes to use for file
            conversion operations.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A dictionary containing the generated response from the language model,
        augmented with the retrieved documents and file context.

    Raises:
        McpError: If either the retrieval or generation step fails.
    """
    if not repo_id_dict:
        repo_id_dict = kc.REPO_ID_DICT
    total_length = 0
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
        for i, doc in enumerate(upload_str_list):
            fragment = (f'[user upload file {i+1} begin]\n'
                        f'{doc}\n[user upload file {i+1} end]')
            if total_length + len(fragment) <= max_tokens:
                upload_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        upload_context = '\n\n'.join(upload_results)

    retrieve_response = await multi_retrieve(
        user_query=user_query,
        retrieve_url=retrieve_url,
        repo_id_dict=repo_id_dict,
        page_num=page_num,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
        rerank_url=rerank_url,
        rerank_batch_size=rerank_batch_size,
        score_threshold=score_threshold,
        top_n=top_n,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    retrieve_results = []
    for i, doc in enumerate(retrieve_response.get('doc_list', [])):
        header = f"[document {i+1} begin] {doc['title']}"
        body = (f"{doc['subtitle']}\n{doc['content']}"
                if doc.get('subtitle') else doc.get('content', ''))
        fragment = f'{header}\n{body} [document {i+1} end]'
        if total_length + len(fragment) <= max_tokens:
            retrieve_results.append(fragment)
            total_length += len(fragment)
        else:
            break
    retrieve_context = '\n\n'.join(retrieve_results)
    if obs_file_list:
        user_query = get_prompt(
            prompt_file, 'user/retrieval_file',
            {'retrieve_results': retrieve_context,
             'upload_context': upload_context, 'user_query': user_query})
    else:
        user_query = get_prompt(
            prompt_file, 'user/retrieval',
            {'retrieve_results': retrieve_context, 'user_query': user_query})
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
    phyto_response['choices'][0]['message'].update(retrieve_response)
    return phyto_response


async def retrieve_generate(
    user_query: str,
    retrieve_url: str = kc.RETRIEVE_URL,
    repo_id: str = kc.REPO_ID,
    page_num: int = kc.PAGE_NUM,
    page_size: int = kc.PAGE_SIZE,
    filter_string: Optional[str] = kc.FILTER_STRING,
    scope: str = kc.SCOPE,
    extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
    rerank_url: str = kc.RERANK_URL,
    rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
    score_threshold: float = kc.SCORE_THRESHOLD,
    prompt_file: str = kc.PROMPT_FILE,
    prompt_path: str = kc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = kc.FREQUENCY_PENALTY,
    max_tokens: int = kc.MAX_TOKENS,
    n: int = kc.N,
    presence_penalty: float = kc.PRESENCE_PENALTY,
    reasoning_effort: str = kc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = kc.RESPONSE_FORMAT,
    stream: bool = kc.STREAM,
    temperature: float = kc.TEMPERATURE,
    top_p: float = kc.TOP_P,
    user: str = kc.USER,
    timeout: float = kc.TIMEOUT,
    retriable_codes: List[int] = kc.RETRIABLE_CODES,
    max_retries: int = kc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Perform retrieval-augmented generation (RAG) with single repository.

    This function first retrieves relevant documents using `retrieve`,
    then uses the retrieved documents to augment a prompt for a language
    model to generate a response. Optionally processes user-uploaded files
    from OBS to provide additional context for the query.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id: The ID of the knowledge repository to search.
        page_num: The page number for pagination of retrieval results.
        page_size: The number of documents to retrieve per page.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        prompt_file: The path to the prompt template file.
        prompt_path: The path to the specific prompt within the template file.
        api_key: The API key for the language model.
        base_url: The base URL for the language model service.
        model: The ID of the language model to use.
        frequency_penalty: The frequency penalty for the language model.
        max_tokens: The maximum number of tokens to generate.
        n: The number of chat completion choices to generate.
        presence_penalty: The presence penalty for the language model.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired response format from the language model.
        stream: Whether to stream the response from the language model.
        temperature: The temperature for the language model.
        top_p: The top_p for the language model.
        user: The user ID for the language model.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A dictionary containing the generated response from the language model,
        augmented with the retrieved documents and file context.

    Raises:
        McpError: If either the retrieval or generation step fails.
    """
    retrieve_response = await retrieve(
        user_query=user_query,
        retrieve_url=retrieve_url,
        repo_id=repo_id,
        page_num=page_num,
        page_size=page_size,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
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
        header = f"[document {i+1} begin] {doc['title']}"
        body = (f"{doc['subtitle']}\\n{doc['content']}"
                if doc.get('subtitle') else doc.get('content', ''))
        fragment = f'{header}\\n{body} [document {i+1} end]'
        if total_length + len(fragment) <= max_tokens:
            retrieve_results.append(fragment)
            total_length += len(fragment)
        else:
            break
    retrieve_context = '\\n\\n'.join(retrieve_results)
    user_query = get_prompt(
        prompt_file, 'user/protocol',
        {'retrieve_results': retrieve_context, 'experiment': user_query})
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
    phyto_response['choices'][0]['message'].update(retrieve_response)
    return phyto_response


async def rerank(user_query: str,
                 doc_list: List[Dict[str, Any]],
                 rerank_url: str = kc.RERANK_URL,
                 top_n: int = kc.TOP_N,
                 rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
                 score_threshold: float = kc.SCORE_THRESHOLD,
                 timeout: float = kc.TIMEOUT,
                 retriable_codes: List[int] = kc.RETRIABLE_CODES,
                 max_retries: int = kc.MAX_RETRIES,
                 ) -> list:
    """Rerank a list of documents based on a user query.

    This function sends a list of documents to a reranking service to obtain
    relevance scores. It processes documents in batches and filters the
    results based on a score threshold.

    Args:
        user_query: The user's natural language query.
        doc_list: A list of document dictionaries to be reranked.
        rerank_url: The URL of the reranking service.
        top_n: The number of top-scoring documents to return.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A list of reranked document dictionaries, sorted by score in
        descending order.

    Raises:
        McpError: If the API call to the reranking service fails after all
                  retries.
    """
    async def make_rerank_request(client, docs_batch):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    rerank_url,
                    headers={'Content-Type': 'application/json'},
                    json={'query': user_query,
                          'ranking_order': ['title', 'content'],
                          'docs': docs_batch,
                          'top_n': top_n},
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()['rank_result']

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
                    message=f'Failed to rerank: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e

    docs, id_doc_dict = [], {}
    for doc in doc_list:
        if doc['chunk_id'] not in id_doc_dict:
            docs.append({
                'id': doc['chunk_id'],
                'title': doc['title'],
                'content': doc['content'],
            })
            id_doc_dict.update({doc['chunk_id']: doc})

    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        if len(docs) > rerank_batch_size:
            chunks = split_list(docs, rerank_batch_size)
            tasks = [make_rerank_request(client, chunk) for chunk in chunks]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results = []
            for result in results:
                all_results.extend(result)
            rank_docs = sorted(
                all_results, key=lambda x: x['score'], reverse=True)[:top_n]
        else:
            rank_docs = await make_rerank_request(client, docs)
    return [
        {**id_doc_dict[doc['id']].copy(), 'score': doc['score']}
        for doc in rank_docs
        if doc['score'] >= score_threshold
    ]


def response_to_string(phyto_response: dict) -> str:
    """Convert a RAG response to a formatted string with references.

    This function extracts the generated content and document list from a
    retrieval-augmented generation response and formats it as a readable
    string with numbered references.

    Args:
        phyto_response: A dictionary containing the response from a RAG
            operation. Expected to have the structure returned by
            `multi_retrieve_generate`, with 'choices' containing message
            content and doc_list.

    Returns:
        A formatted string containing the generated content followed by
        a numbered reference list of the source documents.

    Examples:
        >>> response = {
        ...     'choices': [{
        ...         'message': {
        ...             'content': 'Photosynthesis is...',
        ...             'doc_list': [
        ...                 {'title': 'Plant Biology.pdf'},
        ...                 {'title': 'Botany Research'}
        ...             ]
        ...         }
        ...     }]
        ... }
        >>> result = response_to_string(response)
        >>> print(result)
        Photosynthesis is...

        **Reference:**
        [1] Plant Biology
        [2] Botany Research
    """
    content = phyto_response['choices'][0]['message']['content']
    doc_list = phyto_response['choices'][0]['message']['doc_list']
    doc_string = ''
    for doc_id, doc in enumerate(doc_list):
        title = doc['title']
        if title[-3:] in ('pdf', 'PDF'):
            doc_string += f'[{doc_id+1}] ' + title[:-4] + '\n'
        else:
            doc_string += f'[{doc_id+1}] ' + title + '\n'
    return content + '\n\n**Reference:**\n' + doc_string
