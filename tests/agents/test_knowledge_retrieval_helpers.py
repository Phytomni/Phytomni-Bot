# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the knowledge retrieval pure helpers.

Pins the small synchronous helpers that the retrieve / rerank async
orchestration depends on: _collect_rank_results error handling and
top_n trimming, _rerank_docs dedup + content fallback, _sorted_merged_docs
score-descending merging, _list_or_empty coercion, _timeout shaping,
and the multi-layer clear_retrieval_caches admin seam.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from httpx import Timeout
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge.retrieval import (
    _collect_rank_results,
    _list_or_empty,
    _multi_retrieve,
    _rerank_docs,
    _retrieve_cached,
    _retrieve_scope_docs,
    _sorted_merged_docs,
    _timeout,
    clear_retrieval_caches,
)

pytestmark = pytest.mark.unit


def test_collect_rank_results_flattens_and_sorts_by_score() -> None:
    """Per-batch lists flatten, sort by score desc, then truncate to top_n."""
    batches: List[List[Dict[str, Any]]] = [
        [{"score": 0.4}, {"score": 0.9}],
        [{"score": 0.7}, {"score": 0.2}],
    ]

    result = _collect_rank_results(batches, top_n=3)

    assert [doc["score"] for doc in result] == [0.9, 0.7, 0.4]


def test_collect_rank_results_raises_when_any_batch_is_exception() -> None:
    """A BaseException item in a batch propagates as a wrapped McpError.

    Pins the contract: ``asyncio.gather(return_exceptions=True)`` rerank
    batches must not silently drop a failing batch's slice; the helper
    must surface the failure so the agent caller fails loudly.
    """
    batches: List[Any] = [
        [{"score": 0.5}],
        RuntimeError("rerank pod failed"),
    ]

    with pytest.raises(McpError) as excinfo:
        _collect_rank_results(batches, top_n=10)

    assert "Reranking failed" in excinfo.value.error.message


def test_rerank_docs_dedupes_by_chunk_id_and_uses_big_content_fallback() -> (
    None
):
    """_rerank_docs prefers ``big_content`` and dedupes by ``chunk_id``."""
    doc_list = [
        {
            "chunk_id": "c1",
            "title": "Paper A",
            "big_content": "BIG",
            "content": "small",
        },
        {
            "chunk_id": "c2",
            "title": "Paper B",
            "content": "fallback",
        },
        {
            "chunk_id": "c1",  # duplicate id; skipped
            "title": "Paper A",
            "big_content": "ignored",
        },
        {
            "chunk_id": "c3",
            "title": "Paper C",
        },  # no content -> skipped
    ]

    docs, id_doc_dict = _rerank_docs(doc_list)

    assert [doc["id"] for doc in docs] == ["c1", "c2"]
    assert docs[0]["content"] == "BIG"
    assert docs[1]["content"] == "fallback"
    assert "c1" in id_doc_dict
    assert "c3" not in id_doc_dict


def test_sorted_merged_docs_merges_and_trims_to_top_n() -> None:
    """``_sorted_merged_docs`` merges results then truncates to top_n."""
    results: List[Any] = [
        {"doc_list": [{"score": 0.3}, {"score": 0.8}]},
        {"doc_list": [{"score": 0.5}]},
        {"not_doc_list": "ignored"},
    ]

    sorted_docs = _sorted_merged_docs(results, top_n=2)

    assert [doc["score"] for doc in sorted_docs] == [0.8, 0.5]


def test_sorted_merged_docs_returns_all_when_top_n_is_zero() -> None:
    """A top_n of 0 or None disables truncation."""
    results: List[Any] = [
        {"doc_list": [{"score": 0.2}, {"score": 0.4}]},
    ]

    assert len(_sorted_merged_docs(results, top_n=0)) == 2


def test_list_or_empty_coerces_none_and_iterables() -> None:
    """``_list_or_empty`` returns lists as-is and coerces other iterables."""
    assert _list_or_empty(None) == []
    assert _list_or_empty([1, 2]) == [1, 2]
    assert _list_or_empty((3, 4)) == [3, 4]


def test_timeout_returns_httpx_timeout_with_matching_connect() -> None:
    """``_timeout`` shapes a uniform httpx Timeout for retrieve client use."""
    timeout = _timeout(12.5)

    assert isinstance(timeout, Timeout)
    assert timeout.read == 12.5
    assert timeout.connect == 12.5


def test_clear_retrieval_caches_invokes_each_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clear_retrieval_caches`` calls cache_clear on the three layers.

    Pins the three cooperating layers (_multi_retrieve, _retrieve_cached,
    _retrieve_scope_docs); a dropped clear in any layer would leave a
    stale doc set surfacing on subsequent retrieve calls.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        _multi_retrieve, "cache_clear", lambda: calls.append("multi")
    )
    monkeypatch.setattr(
        _retrieve_cached, "cache_clear", lambda: calls.append("single")
    )
    monkeypatch.setattr(
        _retrieve_scope_docs, "cache_clear", lambda: calls.append("scope")
    )

    clear_retrieval_caches()

    assert calls == ["multi", "single", "scope"]
