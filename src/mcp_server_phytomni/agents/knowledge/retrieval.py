# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cached knowledge retrieval and reranking orchestration.

Typed request options live in :mod:`retrieval_options`; this module retains
the historical public helpers and owns only HTTP orchestration and cache
coordination.
"""

import asyncio
from contextlib import nullcontext
from typing import Any
from weakref import WeakKeyDictionary

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPStatusError,
    Timeout,
    TimeoutException,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ...common.httpx_client import get_async_client
from ...common.lists import split_list
from ...common.relay_client import current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...func_cache import LONG_TTL_SECONDS, func_cache
from .retrieval_options import (
    KNOWLEDGE_CONFIG,
    MultiRetrieveOptions,
    MultiRetrievePayloadOptions,
    RerankOptions,
    RetrieveOptions,
    RetrievePayloadOptions,
    RetryOptions,
)

_RERANK_SEM_STATE: WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = WeakKeyDictionary()


def _rerank_semaphore() -> Any:
    """Return the current loop's rerank semaphore or a null context."""
    cap = KNOWLEDGE_CONFIG.RERANK_CONCURRENCY
    if cap <= 0:
        return nullcontext()
    loop = asyncio.get_running_loop()
    semaphore = _RERANK_SEM_STATE.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(cap)
        _RERANK_SEM_STATE[loop] = semaphore
    return semaphore


def reset_rerank_semaphore_state() -> None:
    """Clear the per-loop rerank semaphore registry."""
    _RERANK_SEM_STATE.clear()


def rerank_semaphore_state_size() -> int:
    """Return the number of live per-loop semaphores."""
    return len(_RERANK_SEM_STATE)


@func_cache(
    key_params=[
        "user_query",
        "repo_id",
        "scope",
        "page_num",
        "page_size",
        "filter_string",
        "extra_repo_ids",
        "score_threshold",
    ],
    ttl=LONG_TTL_SECONDS,
)
# pylint: disable=too-many-arguments
# Cache primitive: every named parameter is part of the @func_cache key.
async def _retrieve_cached(
    user_query: str,
    *,
    repo_id: str,
    scope: str,
    page_num: int,
    page_size: int,
    filter_string: str | None,
    extra_repo_ids: tuple[str, ...],
    top_n: int,
    score_threshold: float,
    options: RetrieveOptions,
) -> dict[str, Any]:
    """Cache the merged retrieve and rerank answer per user query."""
    del repo_id, scope, page_num, page_size, filter_string
    del extra_repo_ids, top_n, score_threshold
    doc_list = await _retrieve_raw_docs(user_query, options)
    return {
        "doc_list": await rerank(
            user_query=user_query,
            doc_list=doc_list,
            rerank_url=options.rerank_url,
            top_n=options.page_size,
            rerank_batch_size=options.rerank_batch_size,
            score_threshold=options.score_threshold,
            timeout=options.timeout,
            retriable_codes=list(options.retriable_codes),
            max_retries=options.max_retries,
        ),
        "total": 10000,
    }


# pylint: enable=too-many-arguments


async def retrieve(user_query: str, **kwargs: Any) -> dict[str, Any]:
    """Retrieve and rerank documents for one user query."""
    options = RetrieveOptions.from_kwargs(kwargs)
    return await _retrieve_cached(
        user_query,
        repo_id=options.payload_options.repo_id,
        scope=options.scope,
        page_num=options.payload_options.page_num,
        page_size=options.payload_options.page_size,
        filter_string=options.payload_options.filter_string,
        extra_repo_ids=tuple(options.payload_options.extra_repo_ids or ()),
        top_n=options.page_size,
        score_threshold=options.score_threshold,
        options=options,
    )


async def _retrieve_raw_docs(
    user_query: str,
    options: RetrieveOptions,
) -> list[dict[str, Any]]:
    """Return raw documents for the configured retrieval scope."""
    async with get_async_client(timeout=_timeout(options.timeout)) as client:
        if options.scope in ("doc", "keyword"):
            docs = await _retrieve_scope_docs(
                client,
                user_query=user_query,
                retrieve_url=options.retrieve_url,
                repo_id=options.payload_options.repo_id,
                scope=options.scope,
                page_num=options.payload_options.page_num,
                page_size=options.payload_options.page_size,
                filter_string=options.payload_options.filter_string,
                extra_repo_ids=tuple(
                    options.payload_options.extra_repo_ids or ()
                ),
                timeout=options.timeout,
                max_retries=options.max_retries,
                retriable_codes=options.retriable_codes,
            )
        elif options.scope == "both":
            docs = await _retrieve_both_scopes(client, user_query, options)
        else:
            raise ValueError(
                "Invalid scope value. Must be 'doc', 'keyword', or 'both'."
            )
    return _list_or_empty(docs)


@func_cache(
    key_params=[
        "user_query",
        "repo_id",
        "scope",
        "page_num",
        "page_size",
        "filter_string",
        "extra_repo_ids",
    ],
    ttl=LONG_TTL_SECONDS,
)
# pylint: disable=too-many-arguments
# Cache primitive: see docs/development/lint-exemptions.md.
async def _retrieve_scope_docs(
    client: AsyncClient,
    *,
    user_query: str,
    retrieve_url: str,
    repo_id: str,
    scope: str,
    page_num: int,
    page_size: int,
    filter_string: str | None,
    extra_repo_ids: tuple[str, ...],
    timeout: float,  # noqa: ASYNC109
    max_retries: int,
    retriable_codes: tuple[int, ...],
) -> Any:
    """Retrieve documents for one knowledge-base scope."""
    body = {
        "repo_id": repo_id,
        "content": user_query,
        "page_num": page_num,
        "page_size": page_size,
        "filter_string": filter_string,
        "scope": scope,
        "extra_repo_ids": list(extra_repo_ids),
    }
    message = "Failed to retrieve knowledge base"
    if relay_mode_enabled():
        result = await current_relay_client().post_json(
            "retrieve/search",
            json_body=body,
            message=message,
            request_timeout=timeout,
        )
    else:
        result = await post_json_with_retries(
            client,
            JsonPostRequest(
                url=retrieve_url,
                headers={"Content-Type": "application/json"},
                json_body=body,
            ),
            JsonPostRetry(
                timeout=timeout,
                max_retries=max_retries,
                retriable_codes=retriable_codes,
                message=message,
            ),
        )
    return result.get("doc_list", []) if isinstance(result, dict) else []


# pylint: enable=too-many-arguments


async def _retrieve_both_scopes(
    client: AsyncClient,
    user_query: str,
    options: RetrieveOptions,
) -> list[dict[str, Any]]:
    """Retrieve and merge document and keyword scopes."""
    tasks = [
        _retrieve_scope_docs(
            client,
            user_query=user_query,
            retrieve_url=options.retrieve_url,
            repo_id=options.payload_options.repo_id,
            scope=scope,
            page_num=options.payload_options.page_num,
            page_size=options.payload_options.page_size,
            filter_string=options.payload_options.filter_string,
            extra_repo_ids=tuple(options.payload_options.extra_repo_ids or ()),
            timeout=options.timeout,
            max_retries=options.max_retries,
            retriable_codes=options.retriable_codes,
        )
        for scope in ("doc", "keyword")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    doc_list: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Retrieval failed: {str(result)}",
                )
            ) from result
        doc_list.extend(_list_or_empty(result))
    return doc_list


@func_cache(
    key_params=["user_query", "repo_items", "top_n"],
    ttl=LONG_TTL_SECONDS,
)
async def _multi_retrieve(
    user_query: str,
    repo_items: tuple[tuple[str, int], ...],
    top_n: int,
    *,
    options: MultiRetrieveOptions,
) -> dict[str, Any]:
    """Retrieve, rerank, and merge documents from multiple repositories."""
    del top_n
    try:
        tasks = [
            retrieve(
                user_query=user_query,
                **options.retrieve_kwargs(repo_id, page_size),
            )
            for repo_id, page_size in repo_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        sorted_docs = _sorted_merged_docs(results, options.top_n)
        return {"doc_list": sorted_docs, "total": 10000}
    except (
        ValueError,
        TypeError,
        HTTPStatusError,
        ConnectError,
        TimeoutException,
    ) as exc:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"Multi-retrieve operation failed: {str(exc)}",
            )
        ) from exc


async def multi_retrieve(
    user_query: str,
    repo_id_dict: dict[str, int] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Retrieve and merge documents from multiple repositories."""
    if repo_id_dict is None:
        repo_id_dict = dict(KNOWLEDGE_CONFIG.REPO_ID_DICT)
    options = MultiRetrieveOptions.from_kwargs(kwargs)
    repo_items = tuple(sorted(dict(repo_id_dict).items()))
    if semaphore is not None:
        async with semaphore:
            return await _multi_retrieve(
                user_query,
                repo_items,
                options.top_n,
                options=options,
            )
    return await _multi_retrieve(
        user_query,
        repo_items,
        options.top_n,
        options=options,
    )


async def rerank(
    user_query: str,
    doc_list: list[dict[str, Any]],
    **kwargs: Any,
) -> list:
    """Rerank documents by score and apply the configured threshold."""
    options = RerankOptions.from_kwargs(kwargs)
    docs, id_doc_dict = _rerank_docs(doc_list)
    async with get_async_client(timeout=_timeout(options.timeout)) as client:
        rank_docs = await _rank_docs(client, user_query, docs, options)
    return [
        {**id_doc_dict[doc["id"]].copy(), "score": doc["score"]}
        for doc in _list_or_empty(rank_docs)
        if doc["score"] >= options.score_threshold
    ]


async def _rank_docs(
    client: AsyncClient,
    user_query: str,
    docs: list[dict[str, Any]],
    options: RerankOptions,
) -> Any:
    """Rank docs, batching when the configured limit is exceeded."""
    if len(docs) > options.rerank_batch_size:
        chunks = split_list(docs, options.rerank_batch_size)
        tasks = [
            _rerank_batch(
                client,
                user_query=user_query,
                docs_batch=chunk,
                rerank_url=options.rerank_url,
                top_n=options.top_n,
                timeout=options.timeout,
                max_retries=options.max_retries,
                retriable_codes=options.retriable_codes,
            )
            for chunk in chunks
        ]
        return _collect_rank_results(
            await asyncio.gather(*tasks, return_exceptions=True),
            options.top_n,
        )
    return await _rerank_batch(
        client,
        user_query=user_query,
        docs_batch=docs,
        rerank_url=options.rerank_url,
        top_n=options.top_n,
        timeout=options.timeout,
        max_retries=options.max_retries,
        retriable_codes=options.retriable_codes,
    )


# pylint: disable=too-many-arguments
# Cache primitive: see docs/development/lint-exemptions.md.
async def _rerank_batch(
    client: AsyncClient,
    *,
    user_query: str,
    docs_batch: list[dict[str, Any]],
    rerank_url: str,
    top_n: int,
    timeout: float,  # noqa: ASYNC109
    max_retries: int,
    retriable_codes: tuple[int, ...],
) -> list[dict[str, Any]]:
    """Send one rerank request batch under the per-loop semaphore."""
    async with _rerank_semaphore():
        body = {
            "query": user_query,
            "ranking_order": ["title", "content"],
            "docs": docs_batch,
            "top_n": top_n,
        }
        if relay_mode_enabled():
            result = await current_relay_client().post_json(
                "rerank/rank",
                json_body=body,
                message="Failed to rerank",
                request_timeout=timeout,
            )
        else:
            result = await post_json_with_retries(
                client,
                JsonPostRequest(
                    url=rerank_url,
                    headers={"Content-Type": "application/json"},
                    json_body=body,
                ),
                JsonPostRetry(
                    timeout=timeout,
                    max_retries=max_retries,
                    retriable_codes=retriable_codes,
                    message="Failed to rerank",
                ),
            )
        return (
            result.get("rank_result", []) if isinstance(result, dict) else []
        )


# pylint: enable=too-many-arguments


def _collect_rank_results(
    results: list[Any], top_n: int
) -> list[dict[str, Any]]:
    """Flatten rank results or raise on a failed batch."""
    all_results: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, BaseException):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Reranking failed: {str(result)}",
                )
            ) from result
        all_results.extend(_list_or_empty(result))
    return sorted(all_results, key=lambda item: item["score"], reverse=True)[
        :top_n
    ]


def _rerank_docs(
    doc_list: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[Any, dict[str, Any]]]:
    """Build rerank API docs and an ID lookup map."""
    docs, id_doc_dict = [], {}
    for doc in doc_list:
        chunk_id = doc["chunk_id"]
        if chunk_id in id_doc_dict:
            continue
        content = doc.get("big_content") or doc.get("content")
        if content is None:
            continue
        docs.append(
            {"id": chunk_id, "title": doc["title"], "content": content}
        )
        id_doc_dict[chunk_id] = doc
    return docs, id_doc_dict


def _sorted_merged_docs(
    results: list[Any], top_n: int
) -> list[dict[str, Any]]:
    """Merge retrieve results and sort them by score."""
    merged_docs = []
    for result in results:
        if isinstance(result, dict) and "doc_list" in result:
            merged_docs.extend(result["doc_list"])
    sorted_docs = sorted(
        merged_docs, key=lambda item: item["score"], reverse=True
    )
    if top_n is not None and top_n > 0:
        return sorted_docs[:top_n]
    return sorted_docs


def _list_or_empty(value: Any) -> list:
    """Return list values as-is and treat ``None`` as empty."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _timeout(timeout: float) -> Timeout:
    """Return an httpx timeout with matching connect timeout."""
    return Timeout(timeout, connect=timeout)


def clear_retrieval_caches() -> None:
    """Drop all composite and primitive retrieval caches."""
    _multi_retrieve.cache_clear()
    _retrieve_cached.cache_clear()
    _retrieve_scope_docs.cache_clear()


__all__ = [
    "KNOWLEDGE_CONFIG",
    "MultiRetrieveOptions",
    "MultiRetrievePayloadOptions",
    "RerankOptions",
    "RetrieveOptions",
    "RetrievePayloadOptions",
    "RetryOptions",
    "clear_retrieval_caches",
    "multi_retrieve",
    "rerank",
    "retrieve",
]
