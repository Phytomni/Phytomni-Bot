# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed option records shared by knowledge retrieval and reranking."""

from dataclasses import dataclass
from typing import Any

from ...config.defaults import KnowledgeConfig

KNOWLEDGE_CONFIG = KnowledgeConfig()


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
    filter_string: str | None = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MultiRetrievePayloadOptions:
    """Payload settings for multi-repository retrieval."""

    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM
    filter_string: str | None = KNOWLEDGE_CONFIG.FILTER_STRING
    scope: str = KNOWLEDGE_CONFIG.SCOPE
    extra_repo_ids: tuple[str, ...] | None = None


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
    def from_kwargs(cls, values: dict[str, Any]) -> "RetrieveOptions":
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

    def payload(self, user_query: str, scope: str) -> dict[str, Any]:
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
    def from_kwargs(cls, values: dict[str, Any]) -> "MultiRetrieveOptions":
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

    def retrieve_kwargs(self, repo_id: str, page_size: int) -> dict[str, Any]:
        """Return keyword arguments for one retrieve call."""
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
    def from_kwargs(cls, values: dict[str, Any]) -> "RerankOptions":
        """Build options from keyword-compatible overrides."""
        options = dict(values)
        if options.get("retriable_codes") is None:
            options.pop("retriable_codes", None)
        else:
            options["retriable_codes"] = tuple(options["retriable_codes"])
        return cls(**options)
