# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cached knowledge retrieval and reranking helpers.

This module exposes retrieval option models plus `retrieve`,
`multi_retrieve`, and `rerank` for cached document search and reranking.
"""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
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
from ...config.defaults import KnowledgeConfig
from ...config.relay_mode import relay_mode_enabled
from ...func_cache import LONG_TTL_SECONDS, func_cache

KNOWLEDGE_CONFIG = KnowledgeConfig()

# Per-event-loop rerank concurrency limiter. ``asyncio.Semaphore`` binds
# to the loop on first use, so a module-level singleton would raise
# "bound to a different event loop" across the project's MCP serve loop,
# API lifespan loop, and per-test loops. A WeakKeyDictionary keyed by the
# running loop hands each loop its own semaphore and auto-evicts the entry
# when the loop is garbage-collected (no id(loop) reuse trap).
_RERANK_SEM_STATE: WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = WeakKeyDictionary()


def _rerank_semaphore() -> Any:
    """Return the current loop's rerank semaphore, or a nullcontext.

    Reads the deployment-level ``RERANK_CONCURRENCY`` cap from the
    module-level config. A cap of 0 or less disables throttling and
    returns ``contextlib.nullcontext()`` so callers can ``async with``
    it unconditionally.

    Returns:
        An async context manager: a per-loop ``asyncio.Semaphore`` when
        throttling is enabled, otherwise a ``nullcontext``.
    """
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
    """Clear the per-loop rerank semaphore registry (test/admin helper)."""
    _RERANK_SEM_STATE.clear()


def rerank_semaphore_state_size() -> int:
    """Return the count of live per-loop semaphores (test/admin helper)."""
    return len(_RERANK_SEM_STATE)


@dataclass(frozen=True)
class RetryOptions:
    """HTTP retry settings shared by retrieval requests.

    Attributes:
        timeout: HTTP timeout in seconds.
        retriable_codes: HTTP status codes that trigger retries.
        max_retries: Maximum retry attempts.
    """

    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT
    retriable_codes: tuple[int, ...] = tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES


@dataclass(frozen=True)
class RetrievePayloadOptions:
    """Payload settings for one repository retrieval request.

    Attributes:
        repo_id: Repository ID to search.
        page_num: Page number requested from the retrieval service.
        page_size: Number of documents requested from one repository.
        filter_string: Optional metadata filter expression.
        scope: Retrieval scope, such as doc, keyword, or both.
        extra_repo_ids: Optional extra repository IDs included in search.
    """

    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID
    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE
    filter_string: str | None = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MultiRetrievePayloadOptions:
    """Payload settings for multi-repository retrieval.

    Attributes:
        page_num: Page number requested from the retrieval service.
        filter_string: Optional metadata filter expression.
        scope: Retrieval scope, such as doc, keyword, or both.
        extra_repo_ids: Optional extra repository IDs included in search.
    """

    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM
    filter_string: str | None = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RetrieveOptions:
    """Resolved options for one retrieval request.

    Attributes:
        retrieve_url: Retrieval service endpoint URL.
        payload_options: Repository and payload settings.
        rerank_url: Rerank service endpoint URL.
        rerank_batch_size: Maximum docs per rerank request.
        score_threshold: Minimum accepted rerank score.
        retry_options: HTTP retry settings.
    """

    retrieve_url: str = KNOWLEDGE_CONFIG.RETRIEVE_URL
    payload_options: RetrievePayloadOptions = RetrievePayloadOptions()
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD
    retry_options: RetryOptions = RetryOptions()

    @classmethod
    def from_kwargs(cls, values: dict[str, Any]):
        """Build options from keyword-compatible overrides.

        Args:
            values: Keyword-compatible retrieval and retry overrides.

        Returns:
            Resolved retrieval options.
        """
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
        """Return the retrieval page size.

        Returns:
            Number of documents requested from one repository.
        """
        return self.payload_options.page_size

    @property
    def scope(self) -> str:
        """Return the retrieval scope.

        Returns:
            Retrieval scope such as doc, keyword, or both.
        """
        return self.payload_options.scope

    @property
    def timeout(self) -> float:
        """Return the HTTP timeout.

        Returns:
            HTTP timeout in seconds.
        """
        return self.retry_options.timeout

    @property
    def retriable_codes(self) -> tuple[int, ...]:
        """Return retryable HTTP status codes.

        Returns:
            HTTP status codes that trigger retries.
        """
        return self.retry_options.retriable_codes

    @property
    def max_retries(self) -> int:
        """Return max HTTP retry attempts.

        Returns:
            Maximum retry attempts for retrieval and rerank calls.
        """
        return self.retry_options.max_retries

    def payload(self, user_query: str, scope: str) -> dict[str, Any]:
        """Return the HTTP JSON payload for one retrieve scope.

        Args:
            user_query: User query to send to the retrieval service.
            scope: Retrieval scope for this payload.

        Returns:
            JSON payload for one retrieval service request.
        """
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
    """Resolved options for multiple repository retrieval.

    Attributes:
        retrieve_url: Retrieval service endpoint URL.
        payload_options: Multi-repository payload settings.
        rerank_url: Rerank service endpoint URL.
        rerank_batch_size: Maximum docs per rerank request.
        score_threshold: Minimum accepted rerank score.
        top_n: Maximum number of merged documents to keep.
        retry_options: HTTP retry settings.
    """

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
    def from_kwargs(cls, values: dict[str, Any]):
        """Build options from keyword-compatible overrides.

        Args:
            values: Keyword-compatible multi-retrieval and retry overrides.

        Returns:
            Resolved multi-retrieval options.
        """
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
        """Return the HTTP timeout.

        Returns:
            HTTP timeout in seconds.
        """
        return self.retry_options.timeout

    @property
    def retriable_codes(self) -> tuple[int, ...]:
        """Return retryable HTTP status codes.

        Returns:
            HTTP status codes that trigger retries.
        """
        return self.retry_options.retriable_codes

    @property
    def max_retries(self) -> int:
        """Return max HTTP retry attempts.

        Returns:
            Maximum retry attempts for retrieval and rerank calls.
        """
        return self.retry_options.max_retries

    def retrieve_kwargs(self, repo_id: str, page_size: int) -> dict[str, Any]:
        """Return keyword arguments for a single retrieve call.

        Args:
            repo_id: Repository ID to search.
            page_size: Number of documents requested from the repository.

        Returns:
            Keyword arguments accepted by `retrieve`.
        """
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
    """Resolved options for one rerank request.

    Attributes:
        rerank_url: Rerank service endpoint URL.
        top_n: Maximum number of ranked documents to keep.
        rerank_batch_size: Maximum docs per rerank request.
        score_threshold: Minimum accepted rerank score.
        timeout: HTTP timeout in seconds.
        retriable_codes: HTTP status codes that trigger retries.
        max_retries: Maximum retry attempts.
    """

    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL
    top_n: int = KNOWLEDGE_CONFIG.TOP_N
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD
    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT
    retriable_codes: tuple[int, ...] = tuple(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES

    @classmethod
    def from_kwargs(cls, values: dict[str, Any]):
        """Build options from keyword-compatible overrides.

        Args:
            values: Keyword-compatible rerank and retry overrides.

        Returns:
            Resolved rerank options.
        """
        options = dict(values)
        if options.get("retriable_codes") is None:
            options.pop("retriable_codes", None)
        else:
            options["retriable_codes"] = tuple(options["retriable_codes"])
        return cls(**options)


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
# Cache primitive: every named parameter is part of the @func_cache
# key, so semantic inputs must stay flat. See
# docs/development/lint-exemptions.md.
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
    """Cache the merged retrieve + rerank answer per user query.

    The cache key is anchored on ``user_query`` plus the minimum set
    of semantic parameters needed to keep the result correct (which
    repos are searched, page slice, scope, filter, and score
    threshold). Infrastructure parameters carried inside ``options``
    (URLs, timeouts, retry policy, rerank batching) are deliberately
    excluded so rotating an endpoint or tuning retries never
    invalidates the cached answer. ``options`` is forwarded to the
    cache-miss path that runs ``_retrieve_raw_docs`` + ``rerank``;
    on a hit the cached doc_list is returned directly so the rerank
    HTTP is not paid again.

    ``top_n`` is kept in the signature for ``retrieve()`` wrapper
    compatibility but is *not* in ``key_params``: the wrapper threads
    ``top_n=options.page_size`` so the two parameters always carry
    the same value and using both as cache bits added no
    discrimination. ``page_size`` alone owns the per-call slice size.

    ``_retrieve_scope_docs`` keeps its own primitive cache as a
    second defensive layer: two different ``user_query`` strings that
    happen to share the same (repo, scope, page) tuple still benefit
    from primitive-level dedup. ``_rerank_batch`` no longer caches —
    rerank cost is small compared to retrieval and the composite
    cache here already covers the user-facing repeat-question case.
    """
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
    """Keyword-compatible cached knowledge-base retrieval.

    Thin wrapper that resolves ``RetrieveOptions`` from kwargs and
    forwards to ``_retrieve_cached``. The cache key is anchored on
    ``user_query`` plus the semantic retrieve parameters; URLs /
    timeouts / retries / rerank batching live inside ``options`` and
    are excluded from the key so the cached answer is shared across
    deployments and retry-policy tweaks.

    Args:
        user_query: Query text to search in the knowledge base.
        **kwargs: Keyword-compatible retrieval, rerank, and retry overrides.

    Returns:
        Dictionary with retrieved document list and total count.
    """
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
    """Return raw retrieve docs for the configured search scope."""
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
    timeout: float,
    max_retries: int,
    retriable_codes: tuple[int, ...],
) -> Any:
    """Retrieve docs for a single knowledge-base scope.

    Cached on the semantic inputs only (query, repo, scope, paging,
    filter, extras) using LONG_TTL_SECONDS so identical retrieval
    requests skip the remote HTTP roundtrip. Infrastructure parameters
    — ``client`` / ``timeout`` / ``max_retries`` / ``retriable_codes``
    — are intentionally absent from ``key_params`` so flipping a retry
    policy or rotating the HTTP client does not invalidate the cache.

    ``retrieve_url`` is also infrastructure and is excluded from the
    key: the composite ``_retrieve_cached`` and ``_multi_retrieve``
    layers above already exclude URLs, so a deploy URL rotation always
    short-circuits on the composite layer first and would never reach
    this primitive — keeping it in the key here was a dead bit that
    contradicted the documented "key is semantic input only" policy.
    """
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
            "retrieve/search", json_body=body, message=message
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
    """Retrieve and merge docs from document and keyword scopes."""
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
    """Retrieve from multiple repositories and cache the merged result.

    The composite cache key is anchored on ``user_query`` plus the
    sorted ``repo_items`` (repo id + page size pairs) and the merged
    ``top_n`` so identical user-facing requests collapse into one
    stored answer. ``options`` carries the rerank URL / retry / timeout
    knobs into the cache-miss path but is excluded from ``key_params``
    so deploys can rotate those without invalidating the cached
    answer. On a miss the helper fans out per-repo ``retrieve`` calls
    (themselves cached at the ``_retrieve_cached`` layer) and merges
    the results; on a hit the merged doc_list returns directly.
    """
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
    """Keyword-compatible cached retrieval from multiple repositories.

    Args:
        user_query: Query text to search across repositories.
        repo_id_dict: Optional mapping of repository IDs to page sizes.
        semaphore: Optional semaphore limiting concurrent multi-retrieval.
        **kwargs: Keyword-compatible retrieval, rerank, and retry overrides.

    Returns:
        Dictionary with merged document list and total count.
    """
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
    """Rerank a list of documents based on a user query.

    Args:
        user_query: Query text used for reranking.
        doc_list: Documents to rerank.
        **kwargs: Keyword-compatible rerank and retry overrides.

    Returns:
        Documents sorted by rerank score and filtered by threshold.
    """
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
):
    """Rank docs, batching when needed.

    Rerank batches are no longer individually cached: the composite
    ``_retrieve_cached`` layer above already memoizes the full
    rerank-merged answer per user query, so an additional per-batch
    cache only adds storage churn for a cheap operation. On a cache
    miss in the composite layer this helper still fans out batches
    in parallel against the rerank HTTP endpoint.
    """
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
    timeout: float,
    max_retries: int,
    retriable_codes: tuple[int, ...],
):
    """Send one rerank request batch (uncached).

    Caching the rerank step individually was removed in favor of the
    composite ``_retrieve_cached`` cache one layer up: rerank cost is
    small compared to retrieval, and the composite cache already
    suppresses repeat work whenever the user issues the same query
    again. Keeping a per-batch cache here would only add SQLite
    maintenance load without measurable savings.

    The body runs under the per-loop rerank semaphore so a fan-out (e.g.
    ReviewAgent across dimensions x repos x batches) cannot overload the
    rerank backend; ``async with`` releases the permit on the error path
    too, so a retry-exhausted failure never leaks a slot.
    """
    async with _rerank_semaphore():
        body = {
            "query": user_query,
            "ranking_order": ["title", "content"],
            "docs": docs_batch,
            "top_n": top_n,
        }
        if relay_mode_enabled():
            result = await current_relay_client().post_json(
                "rerank/rank", json_body=body, message="Failed to rerank"
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
    """Flatten rank results or raise on batch errors."""
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
    """Return list values as-is, convert iterables, and treat None as empty."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _timeout(timeout: float) -> Timeout:
    """Return an httpx timeout with matching connect timeout."""
    return Timeout(timeout, connect=timeout)


def clear_retrieval_caches() -> None:
    """Drop cached retrieval results across all layers.

    The retrieval stack now caches in three places: the composite
    ``_multi_retrieve`` layer (per user_query + repo set + top_n),
    the per-repo ``_retrieve_cached`` layer (per user_query + repo
    page slice), and the underlying ``_retrieve_scope_docs`` HTTP
    primitive. All three are cleared together because they cooperate
    on the same call path and are always purged as a unit.
    """
    _multi_retrieve.cache_clear()
    _retrieve_cached.cache_clear()
    _retrieve_scope_docs.cache_clear()
