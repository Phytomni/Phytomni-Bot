# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Strict outcome tests for Knowledge retrieval orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from httpx import TimeoutException
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge import retrieval as retrieval_mod
from mcp_server_phytomni.agents.knowledge.retrieval import (
    _retrieve_cached,
    _RetrieveCacheKey,
)
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RetrieveOptions,
)
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RETRIEVAL_UNAVAILABLE_MESSAGE,
    require_retrieval_docs,
)

pytestmark = pytest.mark.unit


def _doc(chunk_id: str, *, score: float | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "chunk_id": chunk_id,
        "title": f"Title {chunk_id}",
        "content": f"Content {chunk_id}",
    }
    if score is not None:
        doc["score"] = score
    return doc


def _complete_docs(chunk_id: str) -> list[dict[str, Any]]:
    return [_doc(chunk_id)]


def _no_match() -> list[dict[str, Any]]:
    return []


def _cache_key(query: str, *, page_size: int = 3) -> _RetrieveCacheKey:
    """Build a current-version cache key for wrapper-level tests."""
    return _RetrieveCacheKey(
        contract_version=2,
        user_query=query,
        repo_id="repo",
        scope="doc",
        page_num=1,
        page_size=page_size,
        filter_string=None,
        extra_repo_ids=(),
        top_n=page_size,
        score_threshold=0.0,
    )


async def _run_both_scopes(
    monkeypatch: pytest.MonkeyPatch,
    children: list[list[dict[str, Any]] | BaseException],
) -> dict[str, Any]:
    async def fake_scope_docs(*_args: Any, **_kwargs: Any) -> Any:
        child = children.pop(0)
        if isinstance(child, BaseException):
            raise child
        return child

    monkeypatch.setattr(retrieval_mod, "_retrieve_scope_docs", fake_scope_docs)
    options = RetrieveOptions.from_kwargs({"scope": "both"})
    retrieve_both = cast(
        Callable[..., Awaitable[dict[str, Any]]],
        getattr(retrieval_mod, "_retrieve_both_scopes"),
    )
    return await retrieve_both(cast(Any, None), "query", options)


@pytest.mark.parametrize(
    ("children", "expected_outcome", "expected_doc_count"),
    [
        (
            [_complete_docs("doc"), _complete_docs("keyword")],
            "complete",
            2,
        ),
        ([_no_match(), _no_match()], "no_match", 0),
        (
            [_complete_docs("doc"), TimeoutException("hidden")],
            "partial",
            1,
        ),
    ],
)
async def test_multi_source_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    children: list[list[dict[str, Any]] | BaseException],
    expected_outcome: str,
    expected_doc_count: int,
) -> None:
    """Classify complete, no-match, and partial source fan-out results."""
    result = await _run_both_scopes(monkeypatch, list(children))

    assert result["outcome"] == expected_outcome
    assert len(result["doc_list"]) == expected_doc_count
    assert result["total"] == expected_doc_count


@pytest.mark.parametrize(
    "children",
    [
        [TimeoutException("one"), RuntimeError("two")],
        [_no_match(), TimeoutException("failed")],
    ],
)
async def test_zero_reliable_docs_with_failure_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    children: list[list[dict[str, Any]] | BaseException],
) -> None:
    """Fail when source errors leave no reliable evidence."""
    with pytest.raises(McpError) as excinfo:
        await _run_both_scopes(monkeypatch, list(children))

    assert excinfo.value.error.message == RETRIEVAL_UNAVAILABLE_MESSAGE


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"doc_list": "not-a-list"},
        {"doc_list": [{"title": "title", "content": "content"}]},
        {"doc_list": [{"chunk_id": "id", "content": "content"}]},
        {"doc_list": [{"chunk_id": "id", "title": "title"}]},
        {
            "doc_list": [
                {
                    "chunk_id": "id",
                    "title": "title",
                    "content": "content",
                    "score": float("nan"),
                }
            ]
        },
    ],
)
def test_require_retrieval_docs_rejects_protocol_failures(
    payload: Any,
) -> None:
    """Reject missing fields, invalid types, and non-finite scores."""
    with pytest.raises(ValueError, match="Invalid retrieval response"):
        require_retrieval_docs(payload)


def test_require_retrieval_docs_returns_detached_copies() -> None:
    """Provider documents cannot alias orchestration-owned state."""
    original = _doc("detached")

    docs = require_retrieval_docs({"doc_list": [original]})
    docs[0]["title"] = "changed"

    assert original["title"] == "Title detached"


async def test_cancelled_scope_is_re_raised_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve task cancellation instead of projecting a domain error."""
    cancelled = asyncio.CancelledError("cancel")

    with pytest.raises(asyncio.CancelledError):
        await _run_both_scopes(monkeypatch, [cancelled, _no_match()])


async def test_public_error_does_not_expose_failure_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep URLs, queries, tokens, and bodies out of the public failure."""
    secrets = (
        "https://private.invalid/search",
        "secret-query",
        "token-123",
        "private-body",
    )
    failure = RuntimeError(" ".join(secrets))

    with pytest.raises(McpError) as excinfo:
        await _run_both_scopes(monkeypatch, [failure, _no_match()])

    public_message = excinfo.value.error.message
    assert public_message == RETRIEVAL_UNAVAILABLE_MESSAGE
    assert not any(secret in public_message for secret in secrets)


async def test_rerank_failure_returns_stable_partial_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use deterministic source-score evidence when reranking fails."""
    raw_result = {
        "doc_list": [
            _doc("missing"),
            _doc("high", score=0.9),
            _doc("tie-one", score=0.5),
            _doc("tie-two", score=0.5),
        ],
        "total": 4,
        "outcome": "complete",
        "failures": [],
    }

    async def fake_raw(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return raw_result

    async def fake_rerank(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise TimeoutException("private rerank body")

    monkeypatch.setattr(retrieval_mod, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(retrieval_mod, "rerank", fake_rerank)
    options = RetrieveOptions.from_kwargs({"page_size": 3})
    cache_key = _RetrieveCacheKey(
        contract_version=2,
        user_query="query",
        repo_id="repo",
        scope="doc",
        page_num=1,
        page_size=3,
        filter_string=None,
        extra_repo_ids=(),
        top_n=3,
        score_threshold=0.0,
    )

    result = await _retrieve_cached.__wrapped__(cache_key, options=options)

    assert result["outcome"] == "partial"
    assert [doc["chunk_id"] for doc in result["doc_list"]] == [
        "high",
        "tie-one",
        "tie-two",
    ]
    assert result["failures"][-1] == {
        "source": "rerank",
        "kind": "timeout",
        "retryable": True,
    }


async def test_rerank_empty_after_partial_retrieval_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject empty rerank evidence when a source already failed."""

    async def fake_raw(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "doc_list": [_doc("raw")],
            "total": 1,
            "outcome": "partial",
            "failures": [
                {
                    "source": "scope:keyword",
                    "kind": "timeout",
                    "retryable": True,
                }
            ],
        }

    async def fake_rerank(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(retrieval_mod, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(retrieval_mod, "rerank", fake_rerank)
    options = RetrieveOptions.from_kwargs({"page_size": 3})
    cache_key = _RetrieveCacheKey(
        contract_version=2,
        user_query="query",
        repo_id="repo",
        scope="doc",
        page_num=1,
        page_size=3,
        filter_string=None,
        extra_repo_ids=(),
        top_n=3,
        score_threshold=0.0,
    )

    with pytest.raises(McpError) as excinfo:
        await _retrieve_cached.__wrapped__(cache_key, options=options)

    assert excinfo.value.error.message == RETRIEVAL_UNAVAILABLE_MESSAGE


async def test_partial_result_is_not_cached_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial result must not hide a later complete source response."""
    calls = {"raw": 0, "rerank": 0}
    raw_results = iter(
        [
            {
                "doc_list": [_doc("partial")],
                "total": 1,
                "outcome": "partial",
                "failures": [
                    {
                        "source": "scope:keyword",
                        "kind": "timeout",
                        "retryable": True,
                    }
                ],
            },
            {
                "doc_list": [_doc("complete")],
                "total": 1,
                "outcome": "complete",
                "failures": [],
            },
        ]
    )

    async def fake_raw(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["raw"] += 1
        return next(raw_results)

    async def fake_rerank(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        calls["rerank"] += 1
        return [_doc(f"ranked-{calls['rerank']}", score=0.9)]

    monkeypatch.setattr(retrieval_mod, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(retrieval_mod, "rerank", fake_rerank)
    retrieval_mod.clear_retrieval_caches()
    options = RetrieveOptions.from_kwargs({"page_size": 3})

    first = await _retrieve_cached(
        _cache_key("partial-cache-recovery"), options=options
    )
    second = await _retrieve_cached(
        _cache_key("partial-cache-recovery"), options=options
    )

    assert first["outcome"] == "partial"
    assert second["outcome"] == "complete"
    assert calls == {"raw": 2, "rerank": 2}


async def test_rerank_partial_fallback_is_recomputed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rerank fallback is a miss and must not mask a later success."""
    calls = {"raw": 0, "rerank": 0}

    async def fake_raw(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["raw"] += 1
        return {
            "doc_list": [_doc("raw", score=0.4)],
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_rerank(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        calls["rerank"] += 1
        if calls["rerank"] == 1:
            raise TimeoutException("secret rerank failure")
        return [_doc("ranked", score=0.9)]

    monkeypatch.setattr(retrieval_mod, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(retrieval_mod, "rerank", fake_rerank)
    retrieval_mod.clear_retrieval_caches()
    options = RetrieveOptions.from_kwargs({"page_size": 3})

    first = await _retrieve_cached(
        _cache_key("rerank-cache-recovery"), options=options
    )
    second = await _retrieve_cached(
        _cache_key("rerank-cache-recovery"), options=options
    )

    assert first["outcome"] == "partial"
    assert second["outcome"] == "complete"
    assert calls == {"raw": 2, "rerank": 2}


async def test_cancellation_does_not_create_a_cache_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation remains a cancellation and cannot be memoized."""
    calls = 0

    async def fake_raw(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError("cancelled")
        return {
            "doc_list": [_doc("recovered")],
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_rerank(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [_doc("ranked", score=0.9)]

    monkeypatch.setattr(retrieval_mod, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(retrieval_mod, "rerank", fake_rerank)
    retrieval_mod.clear_retrieval_caches()
    options = RetrieveOptions.from_kwargs({"page_size": 3})
    key = _cache_key("cancel-cache-recovery")

    with pytest.raises(asyncio.CancelledError):
        await _retrieve_cached(key, options=options)
    result = await _retrieve_cached(key, options=options)

    assert result["outcome"] == "complete"
    assert calls == 2
