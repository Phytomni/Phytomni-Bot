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
import math
from typing import Any, NamedTuple

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
from .retrieval_result import (
    RetrievalFailure,
    RetrievalOutcome,
    RetrievalProtocolError,
    RetrievalResult,
    cacheable_retrieval_result,
    classify_retrieval_failure,
    require_retrieval_docs,
    retrieval_unavailable_error,
)

_RETRIEVAL_CONTRACT_VERSION = 2


class _RetrieveCacheKey(NamedTuple):
    """Semantic fields that identify one merged retrieval answer."""

    contract_version: int
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

    contract_version: int
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


@func_cache(
    key_params=["cache_key"],
    ttl=LONG_TTL_SECONDS,
    cache_if=cacheable_retrieval_result,
)
async def _retrieve_cached(
    cache_key: _RetrieveCacheKey,
    *,
    options: RetrieveOptions,
) -> RetrievalResult:
    """Cache the merged retrieve and rerank answer per user query."""
    user_query = cache_key.user_query
    del cache_key
    raw_result = await _retrieve_raw_docs(user_query, options)
    raw_docs = raw_result["doc_list"]
    if not raw_docs:
        return raw_result
    ranked_result = (
        await asyncio.gather(
            rerank(
                user_query=user_query,
                doc_list=raw_docs,
                rerank_url=options.rerank_url,
                top_n=options.page_size,
                rerank_batch_size=options.rerank_batch_size,
                score_threshold=options.score_threshold,
                timeout=options.timeout,
                retriable_codes=list(options.retriable_codes),
                max_retries=options.max_retries,
            ),
            return_exceptions=True,
        )
    )[0]
    if isinstance(ranked_result, BaseException) and not isinstance(
        ranked_result, Exception
    ):
        raise ranked_result
    if isinstance(ranked_result, Exception):
        fallback_docs = _stable_source_fallback(raw_docs, options.page_size)
        if not fallback_docs:
            raise retrieval_unavailable_error() from ranked_result
        return {
            "doc_list": fallback_docs,
            "total": len(fallback_docs),
            "outcome": "partial",
            "failures": [
                *raw_result["failures"],
                classify_retrieval_failure(ranked_result, "rerank"),
            ],
        }
    ranked_docs = ranked_result
    if not ranked_docs:
        if raw_result["failures"]:
            raise retrieval_unavailable_error()
        return {
            "doc_list": [],
            "total": 0,
            "outcome": "no_match",
            "failures": [],
        }
    return {
        "doc_list": ranked_docs,
        "total": len(ranked_docs),
        "outcome": raw_result["outcome"],
        "failures": list(raw_result["failures"]),
    }


async def retrieve(user_query: str, **kwargs: Any) -> dict[str, Any]:
    """Retrieve and rerank documents for one user query."""
    options = RetrieveOptions.from_kwargs(kwargs)
    payload = options.payload_options
    cache_key = _RetrieveCacheKey(
        contract_version=_RETRIEVAL_CONTRACT_VERSION,
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
        contract_version=_RETRIEVAL_CONTRACT_VERSION,
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
) -> RetrievalResult:
    """Return raw documents for the configured retrieval scope."""
    if options.scope not in ("doc", "keyword", "both"):
        raise ValueError(
            "Invalid scope value. Must be 'doc', 'keyword', or 'both'."
        )
    client = current_outbound_runtime().http.for_pool(
        OutboundPoolName.RETRIEVAL
    )
    if options.scope in ("doc", "keyword"):
        try:
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
        except Exception as exc:
            raise retrieval_unavailable_error() from exc
        return _retrieval_result(docs, [])
    return await _retrieve_both_scopes(client, user_query, options)


@func_cache(
    key_params=["cache_key"],
    ttl=LONG_TTL_SECONDS,
    cache_if=bool,
)
async def _retrieve_scope_docs(
    cache_key: _RetrieveScopeKey,
    request: _RetrieveScopeRequest,
) -> list[dict[str, Any]]:
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
    return require_retrieval_docs(result)


async def _retrieve_both_scopes(
    client: AsyncRequestClient,
    user_query: str,
    options: RetrieveOptions,
) -> RetrievalResult:
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
    failures: list[RetrievalFailure] = []
    for source, result in zip(("scope:doc", "scope:keyword"), results):
        if isinstance(result, BaseException) and not isinstance(
            result, Exception
        ):
            raise result
        if isinstance(result, Exception):
            failures.append(classify_retrieval_failure(result, source))
            continue
        try:
            doc_list.extend(require_retrieval_docs({"doc_list": result}))
        except RetrievalProtocolError as exc:
            failures.append(classify_retrieval_failure(exc, source))
    if failures and not doc_list:
        raise retrieval_unavailable_error()
    return _retrieval_result(doc_list, failures)


async def multi_retrieve(
    user_query: str,
    repo_id_dict: dict[str, int] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    **kwargs: Any,
) -> RetrievalResult:
    """Retrieve and merge documents from multiple repositories."""
    if repo_id_dict is None:
        repo_id_dict = dict(KNOWLEDGE_CONFIG.REPO_ID_DICT)
    options = MultiRetrieveOptions.from_kwargs(kwargs)
    repo_items = tuple(sorted(dict(repo_id_dict).items()))

    async def gather_repositories() -> RetrievalResult:
        """Run repository leaves and recompute the final merge."""
        tasks = [
            retrieve(
                user_query=user_query,
                **options.retrieve_kwargs(repo_id, page_size),
            )
            for repo_id, page_size in repo_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        reliable_results: list[dict[str, Any]] = []
        failures: list[RetrievalFailure] = []
        for (repo_id, _page_size), result in zip(repo_items, results):
            source = f"repo:{repo_id}"
            if isinstance(result, BaseException) and not isinstance(
                result, Exception
            ):
                raise result
            if isinstance(result, Exception):
                failures.append(classify_retrieval_failure(result, source))
                continue
            try:
                docs = require_retrieval_docs(result)
                outcome = result["outcome"]
                child_failures = result["failures"]
            except (KeyError, TypeError, RetrievalProtocolError) as exc:
                failures.append(classify_retrieval_failure(exc, source))
                continue
            if outcome not in ("complete", "no_match", "partial"):
                failures.append(
                    classify_retrieval_failure(
                        RetrievalProtocolError("Invalid retrieval result"),
                        source,
                    )
                )
                continue
            if outcome == "no_match" and docs:
                failures.append(
                    classify_retrieval_failure(
                        RetrievalProtocolError("Invalid retrieval result"),
                        source,
                    )
                )
                continue
            if outcome in ("complete", "partial") and not docs:
                failures.append(
                    classify_retrieval_failure(
                        RetrievalProtocolError("Invalid retrieval result"),
                        source,
                    )
                )
                continue
            if not isinstance(child_failures, list):
                failures.append(
                    classify_retrieval_failure(
                        RetrievalProtocolError("Invalid retrieval result"),
                        source,
                    )
                )
                continue
            reliable_results.append({"doc_list": docs})
            failures.extend(child_failures)

        merged_docs = _sorted_merged_docs(reliable_results, options.top_n)
        if failures and not merged_docs:
            raise retrieval_unavailable_error()
        return _retrieval_result(merged_docs, failures)

    if semaphore is not None:
        async with semaphore:
            return await gather_repositories()
    return await gather_repositories()


async def rerank(
    user_query: str,
    doc_list: list[dict[str, Any]],
    **kwargs: Any,
) -> list[dict[str, Any]]:
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
    return _require_rank_results(result, request.docs_batch)


def _collect_rank_results(
    results: list[Any], top_n: int
) -> list[dict[str, Any]]:
    """Flatten rank results or raise on a failed batch."""
    all_results: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, BaseException) and not isinstance(
            result, Exception
        ):
            raise result
        if isinstance(result, Exception):
            raise retrieval_unavailable_error() from result
        all_results.extend(_list_or_empty(result))
    return sorted(all_results, key=lambda item: item["score"], reverse=True)[
        :top_n
    ]


def _rerank_docs(
    doc_list: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[Any, dict[str, Any]]]:
    """Build rerank API docs and an ID lookup map."""
    docs, id_doc_dict = [], {}
    validated_docs = require_retrieval_docs({"doc_list": doc_list})
    for doc in validated_docs:
        chunk_id = doc["chunk_id"]
        if chunk_id in id_doc_dict:
            continue
        big_content = doc.get("big_content")
        content = (
            big_content if isinstance(big_content, str) else doc.get("content")
        )
        docs.append(
            {"id": chunk_id, "title": doc["title"], "content": content}
        )
        id_doc_dict[chunk_id] = doc
    return docs, id_doc_dict


def _require_rank_results(
    payload: Any,
    docs_batch: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate one rerank payload against the submitted document IDs."""
    if not isinstance(payload, dict) or "rank_result" not in payload:
        raise RetrievalProtocolError("Invalid rerank response")
    rank_result = payload["rank_result"]
    if not isinstance(rank_result, list):
        raise RetrievalProtocolError("Invalid rerank response")
    allowed_ids = {doc["id"] for doc in docs_batch}
    validated: list[dict[str, Any]] = []
    for entry in rank_result:
        if not isinstance(entry, dict) or entry.get("id") not in allowed_ids:
            raise RetrievalProtocolError("Invalid rerank response")
        score = entry.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
        ):
            raise RetrievalProtocolError("Invalid rerank response")
        validated.append(dict(entry))
    return validated


def _retrieval_result(
    docs: list[dict[str, Any]],
    failures: list[RetrievalFailure],
) -> RetrievalResult:
    """Build the strict result for validated source documents."""
    outcome: RetrievalOutcome
    if docs and failures:
        outcome = "partial"
    elif docs:
        outcome = "complete"
    else:
        outcome = "no_match"
    return {
        "doc_list": docs,
        "total": len(docs),
        "outcome": outcome,
        "failures": failures,
    }


def _stable_source_fallback(
    docs: list[dict[str, Any]], top_n: int
) -> list[dict[str, Any]]:
    """Keep reliable source evidence in deterministic score-first order."""

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, doc = item
        score = doc.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return (0, -float(score), index)
        return (1, 0.0, index)

    ordered = [doc.copy() for _, doc in sorted(enumerate(docs), key=sort_key)]
    if top_n > 0:
        return ordered[:top_n]
    return ordered


def _sorted_merged_docs(
    results: list[Any], top_n: int
) -> list[dict[str, Any]]:
    """Merge results with stable score-first ordering."""
    merged_docs: list[dict[str, Any]] = []
    for result in results:
        merged_docs.extend(require_retrieval_docs(result))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, doc = item
        score = doc.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return (0, -float(score), index)
        return (1, 0.0, index)

    sorted_docs = [
        doc for _, doc in sorted(enumerate(merged_docs), key=sort_key)
    ]
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
    """Drop the surviving scoped and reranked retrieval caches."""
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
