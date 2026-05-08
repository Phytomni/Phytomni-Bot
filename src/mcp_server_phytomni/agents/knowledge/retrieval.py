# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cached knowledge retrieval and reranking helpers."""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
from ...config.defaults import KnowledgeConfig
from ...func_cache import func_cache
from ...utils import split_list

KNOWLEDGE_CONFIG = KnowledgeConfig()
RETRIEVE_CACHE_TTL = 300


@dataclass(frozen=True)
class RetryOptions:
    """HTTP retry settings shared by retrieval requests."""

    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT
    retriable_codes: tuple[int, ...] = tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES


@dataclass(frozen=True)
class RetrievePayloadOptions:
    """Payload settings for one repository retrieval request."""

    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID
    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE
    filter_string: Optional[str] = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: Optional[tuple[str, ...]] = None


@dataclass(frozen=True)
class MultiRetrievePayloadOptions:
    """Payload settings for multi-repository retrieval."""

    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM
    filter_string: Optional[str] = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: Optional[tuple[str, ...]] = None


@dataclass(frozen=True)
class RetrieveOptions:
    """Resolved options for one retrieval request."""

    retrieve_url: str = KNOWLEDGE_CONFIG.RETRIEVE_URL
    payload_options: RetrievePayloadOptions = RetrievePayloadOptions()
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD
    retry_options: RetryOptions = RetryOptions()

    @classmethod
    def from_kwargs(cls, values: Dict[str, Any]):
        """Build options from keyword-compatible overrides."""
        options = dict(values)
        if options.get("retriable_codes") is None:
            options.pop("retriable_codes", None)
        else:
            options["retriable_codes"] = tuple(options["retriable_codes"])
        if options.get("extra_repo_ids") is None:
            options.pop("extra_repo_ids", None)
        else:
            options["extra_repo_ids"] = tuple(options["extra_repo_ids"])
        return cls(
            retrieve_url=options.get(
                "retrieve_url", KNOWLEDGE_CONFIG.RETRIEVE_URL
            ),
            payload_options=RetrievePayloadOptions(
                repo_id=options.get("repo_id", KNOWLEDGE_CONFIG.REPO_ID),
                page_num=options.get("page_num", KNOWLEDGE_CONFIG.PAGE_NUM),
                page_size=options.get("page_size", KNOWLEDGE_CONFIG.PAGE_SIZE),
                filter_string=options.get(
                    "filter_string", KNOWLEDGE_CONFIG.FILTER_STRING
                ),
                scope=options.get("scope", KNOWLEDGE_CONFIG.SCOPE),
                extra_repo_ids=options.get("extra_repo_ids"),
            ),
            rerank_url=options.get("rerank_url", KNOWLEDGE_CONFIG.RERANK_URL),
            rerank_batch_size=options.get(
                "rerank_batch_size", KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
            ),
            score_threshold=options.get(
                "score_threshold", KNOWLEDGE_CONFIG.SCORE_THRESHOLD
            ),
            retry_options=RetryOptions(
                timeout=options.get("timeout", KNOWLEDGE_CONFIG.TIMEOUT),
                retriable_codes=options.get(
                    "retriable_codes",
                    tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES),
                ),
                max_retries=options.get(
                    "max_retries", KNOWLEDGE_CONFIG.MAX_RETRIES
                ),
            ),
        )

    @property
    def page_size(self) -> int:
        """Return the retrieval page size."""
        return self.payload_options.page_size

    @property
    def scope(self) -> str:
        """Return the retrieval scope."""
        return self.payload_options.scope

    @property
    def timeout(self) -> float:
        """Return the HTTP timeout."""
        return self.retry_options.timeout

    @property
    def retriable_codes(self) -> tuple[int, ...]:
        """Return retryable HTTP status codes."""
        return self.retry_options.retriable_codes

    @property
    def max_retries(self) -> int:
        """Return max HTTP retry attempts."""
        return self.retry_options.max_retries

    def payload(self, user_query: str, scope: str) -> Dict[str, Any]:
        """Return the HTTP JSON payload for one retrieve scope."""
        return {
            "repo_id": self.payload_options.repo_id,
            "content": user_query,
            "page_num": self.payload_options.page_num,
            "page_size": self.payload_options.page_size,
            "filter_string": self.payload_options.filter_string,
            "scope": scope,
            "extra_repo_ids": list(self.payload_options.extra_repo_ids or ()),
        }


@dataclass(frozen=True)
class MultiRetrieveOptions:
    """Resolved options for multiple repository retrieval."""

    retrieve_url: str = KNOWLEDGE_CONFIG.RETRIEVE_URL
    payload_options: MultiRetrievePayloadOptions = (
        MultiRetrievePayloadOptions()
    )
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD
    top_n: int = KNOWLEDGE_CONFIG.TOP_N
    retry_options: RetryOptions = RetryOptions()

    @classmethod
    def from_kwargs(cls, values: Dict[str, Any]):
        """Build options from keyword-compatible overrides."""
        options = dict(values)
        if options.get("retriable_codes") is None:
            options.pop("retriable_codes", None)
        else:
            options["retriable_codes"] = tuple(options["retriable_codes"])
        if options.get("extra_repo_ids") is None:
            options.pop("extra_repo_ids", None)
        else:
            options["extra_repo_ids"] = tuple(options["extra_repo_ids"])
        return cls(
            retrieve_url=options.get(
                "retrieve_url", KNOWLEDGE_CONFIG.RETRIEVE_URL
            ),
            payload_options=MultiRetrievePayloadOptions(
                page_num=options.get("page_num", KNOWLEDGE_CONFIG.PAGE_NUM),
                filter_string=options.get(
                    "filter_string", KNOWLEDGE_CONFIG.FILTER_STRING
                ),
                scope=options.get("scope", KNOWLEDGE_CONFIG.SCOPE),
                extra_repo_ids=options.get("extra_repo_ids"),
            ),
            rerank_url=options.get("rerank_url", KNOWLEDGE_CONFIG.RERANK_URL),
            rerank_batch_size=options.get(
                "rerank_batch_size", KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
            ),
            score_threshold=options.get(
                "score_threshold", KNOWLEDGE_CONFIG.SCORE_THRESHOLD
            ),
            top_n=options.get("top_n", KNOWLEDGE_CONFIG.TOP_N),
            retry_options=RetryOptions(
                timeout=options.get("timeout", KNOWLEDGE_CONFIG.TIMEOUT),
                retriable_codes=options.get(
                    "retriable_codes",
                    tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES),
                ),
                max_retries=options.get(
                    "max_retries", KNOWLEDGE_CONFIG.MAX_RETRIES
                ),
            ),
        )

    @property
    def timeout(self) -> float:
        """Return the HTTP timeout."""
        return self.retry_options.timeout

    @property
    def retriable_codes(self) -> tuple[int, ...]:
        """Return retryable HTTP status codes."""
        return self.retry_options.retriable_codes

    @property
    def max_retries(self) -> int:
        """Return max HTTP retry attempts."""
        return self.retry_options.max_retries

    def retrieve_kwargs(self, repo_id: str, page_size: int) -> Dict[str, Any]:
        """Return keyword arguments for a single retrieve call."""
        return {
            "retrieve_url": self.retrieve_url,
            "repo_id": repo_id,
            "page_num": self.payload_options.page_num,
            "page_size": page_size,
            "filter_string": self.payload_options.filter_string,
            "scope": self.payload_options.scope,
            "extra_repo_ids": list(self.payload_options.extra_repo_ids or ()),
            "rerank_url": self.rerank_url,
            "rerank_batch_size": self.rerank_batch_size,
            "score_threshold": self.score_threshold,
            "timeout": self.timeout,
            "retriable_codes": list(self.retriable_codes),
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True)
class RerankOptions:
    """Resolved options for one rerank request."""

    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL
    top_n: int = KNOWLEDGE_CONFIG.TOP_N
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD
    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT
    retriable_codes: tuple[int, ...] = tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES

    @classmethod
    def from_kwargs(cls, values: Dict[str, Any]):
        """Build options from keyword-compatible overrides."""
        options = dict(values)
        if options.get("retriable_codes") is None:
            options.pop("retriable_codes", None)
        else:
            options["retriable_codes"] = tuple(options["retriable_codes"])
        return cls(**options)


@func_cache(
    key_params=["user_query", "options"],
    ttl=RETRIEVE_CACHE_TTL,
)
async def _retrieve_cached(
    user_query: str,
    options: RetrieveOptions,
) -> Dict[str, Any]:
    """Retrieve and rerank documents from a knowledge base."""
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


async def retrieve(user_query: str, **kwargs: Any) -> Dict[str, Any]:
    """Keyword-compatible cached knowledge-base retrieval."""
    options = RetrieveOptions.from_kwargs(kwargs)
    return await _retrieve_cached(user_query, options)


async def _retrieve_raw_docs(
    user_query: str,
    options: RetrieveOptions,
) -> List[Dict[str, Any]]:
    """Return raw retrieve docs for the configured search scope."""
    async with AsyncClient(
        timeout=_timeout(options.timeout), verify=False
    ) as client:
        if options.scope in ("doc", "keyword"):
            docs = await _retrieve_scope_docs(
                client, user_query, options, options.scope
            )
        elif options.scope == "both":
            docs = await _retrieve_both_scopes(client, user_query, options)
        else:
            raise ValueError(
                "Invalid scope value. Must be 'doc', 'keyword', or 'both'."
            )
    return _list_or_empty(docs)


async def _retrieve_scope_docs(
    client: AsyncClient,
    user_query: str,
    options: RetrieveOptions,
    scope: str,
) -> Any:
    """Retrieve docs for a single knowledge-base scope."""
    result = await post_json_with_retries(
        client,
        JsonPostRequest(
            url=options.retrieve_url,
            headers={"Content-Type": "application/json"},
            json_body=options.payload(user_query, scope),
        ),
        JsonPostRetry(
            timeout=options.timeout,
            max_retries=options.max_retries,
            retriable_codes=options.retriable_codes,
            message="Failed to retrieve knowledge base",
        ),
    )
    return result.get("doc_list", []) if isinstance(result, dict) else []


async def _retrieve_both_scopes(
    client: AsyncClient,
    user_query: str,
    options: RetrieveOptions,
) -> List[Dict[str, Any]]:
    """Retrieve and merge docs from document and keyword scopes."""
    tasks = [
        _retrieve_scope_docs(client, user_query, options, scope)
        for scope in ("doc", "keyword")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    doc_list: List[Dict[str, Any]] = []
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
    key_params=["user_query", "repo_items", "options"],
    ttl=RETRIEVE_CACHE_TTL,
)
async def _multi_retrieve_cached(
    user_query: str,
    repo_items: tuple[tuple[str, int], ...],
    options: MultiRetrieveOptions,
) -> Dict[str, Any]:
    """Retrieve from multiple repositories and return sorted docs."""
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
    repo_id_dict: Optional[Dict[str, int]] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Keyword-compatible cached retrieval from multiple repositories."""
    if repo_id_dict is None:
        repo_id_dict = dict(KNOWLEDGE_CONFIG.REPO_ID_DICT)
    options = MultiRetrieveOptions.from_kwargs(kwargs)
    repo_items = tuple(sorted(dict(repo_id_dict).items()))
    if semaphore is not None:
        async with semaphore:
            return await _multi_retrieve_cached(
                user_query, repo_items, options
            )
    return await _multi_retrieve_cached(user_query, repo_items, options)


async def rerank(
    user_query: str,
    doc_list: List[Dict[str, Any]],
    **kwargs: Any,
) -> list:
    """Rerank a list of documents based on a user query."""
    options = RerankOptions.from_kwargs(kwargs)
    docs, id_doc_dict = _rerank_docs(doc_list)
    async with AsyncClient(
        timeout=_timeout(options.timeout), verify=False
    ) as client:
        rank_docs = await _rank_docs(client, user_query, docs, options)
    return [
        {**id_doc_dict[doc["id"]].copy(), "score": doc["score"]}
        for doc in _list_or_empty(rank_docs)
        if doc["score"] >= options.score_threshold
    ]


async def _rank_docs(
    client: AsyncClient,
    user_query: str,
    docs: List[Dict[str, Any]],
    options: RerankOptions,
):
    """Rank docs, batching when needed."""
    if len(docs) > options.rerank_batch_size:
        chunks = split_list(docs, options.rerank_batch_size)
        tasks = [
            _rerank_batch(client, user_query, chunk, options)
            for chunk in chunks
        ]
        return _collect_rank_results(
            await asyncio.gather(*tasks, return_exceptions=True),
            options.top_n,
        )
    return await _rerank_batch(client, user_query, docs, options)


async def _rerank_batch(
    client: AsyncClient,
    user_query: str,
    docs_batch: List[Dict[str, Any]],
    options: RerankOptions,
):
    """Send one rerank request batch."""
    result = await post_json_with_retries(
        client,
        JsonPostRequest(
            url=options.rerank_url,
            headers={"Content-Type": "application/json"},
            json_body={
                "query": user_query,
                "ranking_order": ["title", "content"],
                "docs": docs_batch,
                "top_n": options.top_n,
            },
        ),
        JsonPostRetry(
            timeout=options.timeout,
            max_retries=options.max_retries,
            retriable_codes=options.retriable_codes,
            message="Failed to rerank",
        ),
    )
    return result.get("rank_result", []) if isinstance(result, dict) else []


def _collect_rank_results(
    results: List[Any], top_n: int
) -> List[Dict[str, Any]]:
    """Flatten rank results or raise on batch errors."""
    all_results: List[Dict[str, Any]] = []
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
    doc_list: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[Any, Dict[str, Any]]]:
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
    results: List[Any], top_n: int
) -> List[Dict[str, Any]]:
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
    """Return list values as-is, convert iterables, and treat None as empty."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _timeout(timeout: float) -> Timeout:
    """Return an httpx timeout with matching connect timeout."""
    return Timeout(timeout, connect=timeout)


setattr(retrieve, "cache_clear", _retrieve_cached.cache_clear)
setattr(retrieve, "cache_info", _retrieve_cached.cache_info)
setattr(multi_retrieve, "cache_clear", _multi_retrieve_cached.cache_clear)
setattr(multi_retrieve, "cache_info", _multi_retrieve_cached.cache_info)
