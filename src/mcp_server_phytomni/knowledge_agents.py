# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
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
from .utils import get_prompt, split_list

kc = KnowledgeConfig()
sc = SensitiveConfig().load()


async def retrieve(user_query: str,
                   repo_id: str = kc.REPO_ID,
                   page_num: int = kc.PAGE_NUM,
                   page_size: int = kc.PAGE_SIZE,
                   filter_string: Optional[str] = kc.FILTER_STRING,
                   scope: str = kc.SCOPE,
                   extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
                   score_threshold: float = kc.SCORE_THRESHOLD,
                   timeout: float = kc.TIMEOUT,
                   retriable_codes: List[int] = kc.RETRIABLE_CODES,
                   max_retries: int = kc.MAX_RETRIES,
                   ) -> Dict[str, Any]:
    """Retrieve and rerank documents from a knowledge base based on a user
        query.

    This function queries a knowledge base service, potentially searching
    across document content, keywords, or both. Results are then reranked
    to improve relevance. It includes retry mechanisms for transient
    network or server errors.

    Args:
        user_query: The user's natural language query or prompt.
        repo_id: Identifier for the primary knowledge repository to search.
            Defaults to `REPO_ID`.
        page_num: Pagination page number for retrieval results.
            Defaults to `PAGE_NUM`.
        page_size: Number of documents to retrieve per page from the initial
            search, before reranking. This also serves as the `top_n` parameter
            for the reranking process. Defaults to `PAGE_SIZE`.
        filter_string: Optional filter criteria string for metadata filtering
            during the initial retrieval. Defaults to `FILTER_STRING`.
        scope: Search scope, can be 'doc' (document content), 'keyword', or
            'both' (searches both and combines results).
            Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the search. Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied after
            reranking to include documents in the final result.
            Defaults to `SCORE_THRESHOLD`.
        timeout: Total request timeout in seconds for each API call attempt,
            including connection. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            attempt. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls that fail
            with a retriable status code or network error.
            Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the reranked list of documents and a total
        count. Specifically, includes 'doc_list' (a list of reranked documents
        meeting the score threshold) and 'total' (a fixed placeholder value
        of 10000 in the current implementation).

    Raises:
        McpError: If the API call to the retrieval service fails after all
            retry attempts due to HTTP errors or network issues.
        ValueError: If an unsupported `scope` value (other than 'doc',
            'keyword', or 'both') is provided.
    """
    async def make_retrieve_request(client, scope):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    kc.RETRIEVE_URL,
                    headers={"Content-Type": "application/json"},
                    json={"repo_id": repo_id,
                          "content": user_query,
                          "page_num": page_num,
                          "page_size": page_size,
                          "filter_string": filter_string,
                          "scope": scope,
                          "extra_repo_ids": extra_repo_ids},
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
                    message=f"Failed to retrieve knowledge base: {str(e)}"
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
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
            raise ValueError()
    return {"doc_list": await rerank(
                user_query=user_query,
                doc_list=doc_list,
                top_n=page_size,
                rerank_batch_size=kc.RERANK_BATCH_SIZE,
                score_threshold=score_threshold,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            ),
            "total": 10000,
            }


async def multi_retrieve(
    user_query: str,
    repo_id_dict: Optional[Dict[str, int]] = kc.REPO_ID_DICT,
    page_num: int = kc.PAGE_NUM,
    filter_string: Optional[str] = kc.FILTER_STRING,
    scope: str = kc.SCOPE,
    extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
    score_threshold: float = kc.SCORE_THRESHOLD,
    top_n: int = kc.TOP_N,
    timeout: float = kc.TIMEOUT,
    retriable_codes: List[int] = kc.RETRIABLE_CODES,
    max_retries: int = kc.MAX_RETRIES,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Concurrently retrieve and rerank documents from multiple knowledge
        bases.

    This function orchestrates multiple calls to the `retrieve` function,
    one for each repository specified in `repo_id_dict`. It then merges the
    results, sorts them by relevance score, and returns the top N documents.
    An optional semaphore can be used to limit the concurrency of these
    retrieval operations.

    Args:
        user_query: The user's natural language query or prompt.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for retrieval. If None, defaults to
            `REPO_ID_DICT`. Each repository will be queried using its
            specified page size.
        page_num: Pagination page number for retrieval results from each
            repository. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during the initial retrieval from each repository.
            Defaults to `FILTER_STRING`.
        scope: Search scope for each retrieval, can be 'doc', 'keyword', or
            'both'. Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the search for each retrieval task.
            Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied after
            reranking within each individual `retrieve` call. Documents below
            this threshold are filtered out before merging.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The total number of top-scoring documents to return after
            merging and sorting results from all repositories. If `None` or
            non-positive, all merged documents are returned.
            Defaults to `TOP_N`.
        timeout: Total request timeout in seconds for each individual
            `retrieve` API call attempt, including connection.
            Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            attempt for each individual `retrieve` call.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for each individual
            `retrieve` API call. Defaults to `MAX_RETRIES`.
        semaphore: An optional `asyncio.Semaphore` to limit the number of
            concurrent `retrieve` operations. If `None`, no concurrency limit
            is applied beyond system capabilities. Defaults to `None`.

    Returns:
        A dictionary containing:
            - 'doc_list': A list of documents, merged from all specified
              repositories, sorted by their 'score' in descending order,
              and truncated to `top_n` if specified.
            - 'total': A fixed placeholder value (10000 in the current
              implementation).

    Raises:
        McpError: If any underlying `retrieve` operation fails and exhausts
            its retries, or if any other unhandled exception occurs during
            the multi-retrieve process.
    """
    if not repo_id_dict:
        repo_id_dict = kc.REPO_ID_DICT

    async def make_multi_retrieve():
        try:
            tasks = [
                retrieve(
                    user_query=user_query,
                    repo_id=repo_id,
                    page_num=page_num,
                    page_size=page_size,
                    filter_string=filter_string,
                    scope=scope,
                    extra_repo_ids=extra_repo_ids,
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
                "doc_list": sorted_docs,
                "total": 10000,
            }
        except Exception as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Multi-retrieve operation failed: {str(e)}"
            )) from e

    if semaphore is not None:
        async with semaphore:
            return await make_multi_retrieve()
    else:
        return await make_multi_retrieve()


async def multi_retrieve_generate(
    user_query: str,
    repo_id_dict: Optional[Dict[str, int]] = kc.REPO_ID_DICT,
    page_num: int = kc.PAGE_NUM,
    filter_string: Optional[str] = kc.FILTER_STRING,
    scope: str = kc.SCOPE,
    extra_repo_ids: Optional[List[str]] = kc.EXTRA_REPO_IDS,
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
    timeout: float = kc.TIMEOUT,
    retriable_codes: List[int] = kc.RETRIABLE_CODES,
    max_retries: int = kc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Perform retrieval-augmented generation (RAG).

    This function first retrieves relevant documents from multiple knowledge
    bases based on the `user_query` using the `multi_retrieve` function.
    The retrieved documents are then formatted and combined with the original
    `user_query` into a new prompt. This augmented prompt is then passed to
    the `phyto_chat` function to generate a response. The final response from
    `phyto_chat` is augmented with the document list from the retrieval step.

    Args:
        user_query: The user's initial natural language query or prompt, used
            for document retrieval and as a basis for the generation prompt.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for retrieval. If None, defaults to
            `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results from each
            repository. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during the initial retrieval from each repository.
            Defaults to `FILTER_STRING`.
        scope: Search scope for each retrieval, can be 'doc', 'keyword', or
            'both'. Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the search for each retrieval task.
            Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied after
            reranking within each individual `retrieve` call.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The total number of top-scoring documents to retrieve and use
            as context for generation. Defaults to `TOP_N`.
        prompt_file: Path to the prompt template file. Used for constructing
            the system prompt and the user prompt for generation.
            Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for generation. Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for
            generation. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for generation.
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for generation.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in
            generation. Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the completion.
            Defaults to `MAX_TOKENS`.
        n: Number of chat completion choices to generate. Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in generation.
            Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during generation. Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for Phyto models
            during generation. Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for generation.
            Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for generation.
            Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for generation.
            Defaults to `TOP_P`.
        user: Unique session identifier for the end-user. Defaults to `USER`.
        timeout: Total request timeout in seconds for each underlying API call
            (both retrieval and generation). Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying API calls.
            Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary representing the response from the Phyto generation model.
        The 'choices'[0]['message'] part of this dictionary is updated to
        include the 'doc_list' and 'total' fields from the `multi_retrieve`
        operation, providing context about the retrieved documents.

    Raises:
        McpError: If either the `multi_retrieve` step or the `phyto_chat`
            generation step fails after all retry attempts.
    """
    if not repo_id_dict:
        repo_id_dict = kc.REPO_ID_DICT
    retrieve_response = await multi_retrieve(
        user_query=user_query,
        repo_id_dict=repo_id_dict,
        page_num=page_num,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
        score_threshold=score_threshold,
        top_n=top_n,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    retrieve_results = []
    total_length = 0
    for file_id, eachdoc in enumerate(retrieve_response['doc_list']):
        if eachdoc["subtitle"]:
            current_fragment = (
                f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                f'{eachdoc["subtitle"]}\n{eachdoc["content"]} '
                f'[document {file_id+1} end]')
        else:
            current_fragment = (
                f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                f'{eachdoc["content"]} [document {file_id+1} end]')
        if total_length + len(current_fragment) <= max_tokens:
            retrieve_results.append(current_fragment)
            total_length += len(current_fragment)
        else:
            break
    retrieve_results = '\n\n'.join(retrieve_results)
    user_query = get_prompt(
        prompt_file, 'user/retrieval',
        {'retrieve_results': retrieve_results, 'user_query': user_query})
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
    phyto_response['choices'][0]['message'].update(retrieve_response)
    return phyto_response


async def rerank(user_query: str,
                 doc_list: List[Dict[str, Any]],
                 top_n: int = kc.TOP_N,
                 rerank_batch_size: int = kc.RERANK_BATCH_SIZE,
                 score_threshold: float = kc.SCORE_THRESHOLD,
                 timeout: float = kc.TIMEOUT,
                 retriable_codes: List[int] = kc.RETRIABLE_CODES,
                 max_retries: int = kc.MAX_RETRIES,
                 ):
    """Rerank a list of documents based on a user query using an external
        service.

    This function takes an initial list of documents and a user query,
    then sends them to a reranking service to obtain relevance scores.
    Documents are processed in batches if their number exceeds
    `rerank_batch_size`. The results are then filtered by `score_threshold`
    and the top `top_n` documents are returned, sorted by the new scores.
    Original document information is preserved and augmented with the
    reranking score.

    Args:
        user_query: The user's natural language query or prompt used as the
            basis for reranking.
        doc_list: A list of document dictionaries to be reranked. Each
            dictionary is expected to contain at least 'chunk_id' (for unique
            identification), 'title', and 'content'.
        top_n: The number of top-scoring documents to request from the
            reranking service (per batch if applicable) and also the maximum
            number of documents to return in the final list after merging,
            sorting, and thresholding. Defaults to `TOP_N`.
        rerank_batch_size: Maximum number of documents to send in a single
            batch to the reranking service. If `doc_list` is larger, it will
            be split and processed in concurrent batches.
            Defaults to `RERANK_BATCH_SIZE`.
        score_threshold: Minimum relevance score a document must achieve
            after reranking to be included in the returned list.
            Defaults to `SCORE_THRESHOLD`.
        timeout: Total request timeout in seconds for each API call attempt
            to the reranking service, including connection.
            Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            attempt for calls to the reranking service.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls to the
            reranking service that fail with a retriable status code or
            network error. Defaults to `MAX_RETRIES`.

    Returns:
        A list of reranked document dictionaries. Each dictionary is a copy of
        the original document from `doc_list` (identified by 'chunk_id'),
        augmented with a 'score' key indicating its reranked relevance.
        The list is sorted by score in descending order and includes only
        documents meeting the `score_threshold` and up to `top_n` documents.

    Raises:
        McpError: If an API call to the reranking service fails after all
            retry attempts due to HTTP errors or network issues.
    """
    async def make_rerank_request(client, docs_batch):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    kc.RERANK_URL,
                    headers={"Content-Type": "application/json"},
                    json={"query": user_query,
                          "ranking_order": ["title", "content"],
                          "docs": docs_batch,
                          "top_n": top_n},
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
                    message=f"Failed to rerank: {str(e)}"
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e

    docs, id_doc_dict = [], {}
    for doc in doc_list:
        if doc['chunk_id'] not in id_doc_dict:
            docs.append({
                'id': doc['chunk_id'],
                'title': doc['title'],
                'content': doc['content']
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
        # doc_list = []
        # for doc in rank_docs:
        #     if doc['score'] >= score_threshold:
        #         out_doc = id_doc_dict[doc['id']].copy()
        #         out_doc['score'] = doc['score']
        #         doc_list.append(out_doc)
    return [
        {**id_doc_dict[doc['id']].copy(), 'score': doc['score']}
        for doc in rank_docs
        if doc['score'] >= score_threshold
    ]
