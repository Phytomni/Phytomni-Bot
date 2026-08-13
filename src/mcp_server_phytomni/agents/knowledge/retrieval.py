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
from typing import Any, NamedTuple

from httpx import (
    ConnectError,
    HTTPStatusError,
    TimeoutException,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.http import (
    AsyncRequestClient,
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ...common.lists import split_list
from ...common.relay_client import RelayRequestOptions, current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...runtime.outbound import OutboundPoolName, current_outbound_runtime
from .retrieval_options import (
    KNOWLEDGE_CONFIG,
    MultiRetrieveOptions,
    MultiRetrievePayloadOptions,
    RerankOptions,
    RetrieveOptions,
    RetrievePayloadOptions,
    RetryOptions,
)


class _RetrieveCacheKey(NamedTuple):
    """Semantic fields that identify one merged retrieval answer."""

    user_query: str
    repo_id: str
    scope: str
    page_num: int
    page_size: int
    filter_string: str | None
    extra_repo_ids: tuple[str, ...]
    top_n: int
    score_threshold: float


class _RetrieveScopeKey(NamedTuple):
    """Semantic fields that identify one scoped retrieval response."""

    user_query: str
    repo_id: str
    scope: str
    page_num: int
    page_size: int
    filter_string: str | None
    extra_repo_ids: tuple[str, ...]


class _RetrieveScopeRequest(NamedTuple):
    """Transport settings for one scoped retrieval request."""

    client: AsyncRequestClient
    retrieve_url: str
    timeout: float
    max_retries: int
    retriable_codes: tuple[int, ...]


class _RerankBatchRequest(NamedTuple):
    """Transport request fields for one rerank batch."""

    user_query: str
    docs_batch: list[dict[str, Any]]
    rerank_url: str
    top_n: int
    timeout: float
    max_retries: int
    retriable_codes: tuple[int, ...]


@func_cache(key_params=["cache_key"], ttl=LONG_TTL_SECONDS)
async def _retrieve_cached(
    cache_key: _RetrieveCacheKey,
    *,
    options: RetrieveOptions,
) -> dict[str, Any]:
    """Cache the merged retrieve and rerank answer per user query."""
    user_query = cache_key.user_query
    del cache_key
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


async def retrieve(user_query: str, **kwargs: Any) -> dict[str, Any]:
    """Retrieve and rerank documents for one user query."""
    options = RetrieveOptions.from_kwargs(kwargs)
    payload = options.payload_options
    cache_key = _RetrieveCacheKey(
        user_query=user_query,
        repo_id=payload.repo_id,
        scope=options.scope,
        page_num=payload.page_num,
        page_size=payload.page_size,
        filter_string=payload.filter_string,
        extra_repo_ids=tuple(payload.extra_repo_ids or ()),
        top_n=options.page_size,
        score_threshold=options.score_threshold,
    )
    return await _retrieve_cached(
        cache_key,
        options=options,
    )


def _retrieve_scope_key(
    user_query: str,
    options: RetrieveOptions,
    scope: str,
) -> _RetrieveScopeKey:
    """Build the semantic key for one scoped retrieval request."""
    payload = options.payload_options
    return _RetrieveScopeKey(
        user_query=user_query,
        repo_id=payload.repo_id,
        scope=scope,
        page_num=payload.page_num,
        page_size=payload.page_size,
        filter_string=payload.filter_string,
        extra_repo_ids=tuple(payload.extra_repo_ids or ()),
    )


async def _retrieve_raw_docs(
    user_query: str,
    options: RetrieveOptions,
) -> list[dict[str, Any]]:
    """Return raw documents for the configured retrieval scope."""
    if options.scope not in ("doc", "keyword", "both"):
        raise ValueError(
            "Invalid scope value. Must be 'doc', 'keyword', or 'both'."
        )
    client = current_outbound_runtime().http.for_pool(
        OutboundPoolName.RETRIEVAL
    )
    docs: Any = []
    if options.scope in ("doc", "keyword"):
        docs = await _retrieve_scope_docs(
            _retrieve_scope_key(user_query, options, options.scope),
            _RetrieveScopeRequest(
                client=client,
                retrieve_url=options.retrieve_url,
                timeout=options.timeout,
                max_retries=options.max_retries,
                retriable_codes=options.retriable_codes,
            ),
        )
    elif options.scope == "both":
        docs = await _retrieve_both_scopes(client, user_query, options)
    return _list_or_empty(docs)


@func_cache(key_params=["cache_key"], ttl=LONG_TTL_SECONDS)
async def _retrieve_scope_docs(
    cache_key: _RetrieveScopeKey,
    request: _RetrieveScopeRequest,
) -> Any:
    """Retrieve documents for one knowledge-base scope."""
    client = request.client
    user_query = cache_key.user_query
    body = {
        "repo_id": cache_key.repo_id,
        "content": user_query,
        "page_num": cache_key.page_num,
        "page_size": cache_key.page_size,
        "filter_string": cache_key.filter_string,
        "scope": cache_key.scope,
        "extra_repo_ids": list(cache_key.extra_repo_ids),
    }
    message = "Failed to retrieve knowledge base"
    if relay_mode_enabled():
        result = await current_relay_client().post_json(
            "retrieve/search",
            body,
            pool=OutboundPoolName.RETRIEVAL,
            options=RelayRequestOptions(
                message=message,
                request_timeout=request.timeout,
            ),
        )
    else:
        result = await post_json_with_retries(
            client,
            JsonPostRequest(
                url=request.retrieve_url,
                headers={"Content-Type": "application/json"},
                json_body=body,
            ),
            JsonPostRetry(
                timeout=request.timeout,
                max_retries=request.max_retries,
                retriable_codes=request.retriable_codes,
                message=message,
            ),
        )
    return result.get("doc_list", []) if isinstance(result, dict) else []


async def _retrieve_both_scopes(
    client: AsyncRequestClient,
    user_query: str,
    options: RetrieveOptions,
) -> list[dict[str, Any]]:
    """Retrieve and merge document and keyword scopes."""
    tasks = [
        _retrieve_scope_docs(
            _retrieve_scope_key(user_query, options, scope),
            _RetrieveScopeRequest(
                client=client,
                retrieve_url=options.retrieve_url,
                timeout=options.timeout,
                max_retries=options.max_retries,
                retriable_codes=options.retriable_codes,
            ),
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
    client = current_outbound_runtime().http.for_pool(OutboundPoolName.RERANK)
    rank_docs = await _rank_docs(client, user_query, docs, options)
    return [
        {**id_doc_dict[doc["id"]].copy(), "score": doc["score"]}
        for doc in _list_or_empty(rank_docs)
        if doc["score"] >= options.score_threshold
    ]


async def _rank_docs(
    client: AsyncRequestClient,
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
                _RerankBatchRequest(
                    user_query=user_query,
                    docs_batch=chunk,
                    rerank_url=options.rerank_url,
                    top_n=options.top_n,
                    timeout=options.timeout,
                    max_retries=options.max_retries,
                    retriable_codes=options.retriable_codes,
                ),
            )
            for chunk in chunks
        ]
        return _collect_rank_results(
            await asyncio.gather(*tasks, return_exceptions=True),
            options.top_n,
        )
    return await _rerank_batch(
        client,
        _RerankBatchRequest(
            user_query=user_query,
            docs_batch=docs,
            rerank_url=options.rerank_url,
            top_n=options.top_n,
            timeout=options.timeout,
            max_retries=options.max_retries,
            retriable_codes=options.retriable_codes,
        ),
    )


async def _rerank_batch(
    client: AsyncRequestClient,
    request: _RerankBatchRequest,
) -> list[dict[str, Any]]:
    """Send one rerank request batch."""
    body = {
        "query": request.user_query,
        "ranking_order": ["title", "content"],
        "docs": request.docs_batch,
        "top_n": request.top_n,
    }
    if relay_mode_enabled():
        result = await current_relay_client().post_json(
            "rerank/rank",
            body,
            pool=OutboundPoolName.RERANK,
            options=RelayRequestOptions(
                message="Failed to rerank",
                request_timeout=request.timeout,
            ),
        )
    else:
        result = await post_json_with_retries(
            client,
            JsonPostRequest(
                url=request.rerank_url,
                headers={"Content-Type": "application/json"},
                json_body=body,
            ),
            JsonPostRetry(
                timeout=request.timeout,
                max_retries=request.max_retries,
                retriable_codes=request.retriable_codes,
                message="Failed to rerank",
            ),
        )
    return result.get("rank_result", []) if isinstance(result, dict) else []


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
