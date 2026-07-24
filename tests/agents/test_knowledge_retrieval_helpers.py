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

import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields
from typing import Any, cast

import pytest
from httpx import AsyncClient, Timeout
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge import retrieval as retrieval_mod
from mcp_server_phytomni.agents.knowledge.retrieval import (
    RerankOptions,
    _collect_rank_results,
    _list_or_empty,
    _multi_retrieve,
    _rerank_batch,
    _rerank_docs,
    _rerank_semaphore,
    _RerankBatchRequest,
    _retrieve_cached,
    _retrieve_scope_docs,
    _sorted_merged_docs,
    _timeout,
    clear_retrieval_caches,
    rerank_semaphore_state_size,
    reset_rerank_semaphore_state,
)
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RerankOptions as LeafRerankOptions,
)
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RetrieveOptions,
)
from mcp_server_phytomni.config.overrides import RETRIEVAL_CONFIG_FIELD_MAP

pytestmark = pytest.mark.unit


def test_retrieval_facade_reexports_option_models() -> None:
    """The legacy retrieval module remains the option-model facade."""
    assert RerankOptions is LeafRerankOptions


def test_collect_rank_results_flattens_and_sorts_by_score() -> None:
    """Per-batch lists flatten, sort by score desc, then truncate to top_n."""
    batches: list[list[dict[str, Any]]] = [
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
    batches: list[Any] = [
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
    results: list[Any] = [
        {"doc_list": [{"score": 0.3}, {"score": 0.8}]},
        {"doc_list": [{"score": 0.5}]},
        {"not_doc_list": "ignored"},
    ]

    sorted_docs = _sorted_merged_docs(results, top_n=2)

    assert [doc["score"] for doc in sorted_docs] == [0.8, 0.5]


def test_sorted_merged_docs_returns_all_when_top_n_is_zero() -> None:
    """A top_n of 0 or None disables truncation."""
    results: list[Any] = [
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


async def test_retrieve_raw_docs_rejects_unknown_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unsupported retrieval scopes before issuing an HTTP request."""

    @asynccontextmanager
    async def fake_client(**kwargs: Any):
        del kwargs
        yield cast(AsyncClient, None)

    monkeypatch.setattr(retrieval_mod, "get_async_client", fake_client)
    options = RetrieveOptions.from_kwargs({"scope": "unsupported"})

    with pytest.raises(ValueError, match="Invalid scope value"):
        retrieve_raw_docs = getattr(retrieval_mod, "_retrieve_raw_docs")
        await retrieve_raw_docs("query", options)


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


async def _fire_rerank_fan_out(
    monkeypatch: pytest.MonkeyPatch, *, cap: int, calls: int
) -> int:
    """Fire ``calls`` concurrent ``_rerank_batch`` requests under ``cap``.

    Patches the config cap and the direct HTTP egress with a slow stub
    that tracks the live in-flight count, then returns the observed
    peak so callers can assert throttled and unthrottled behavior.
    """
    monkeypatch.setattr(
        retrieval_mod.KNOWLEDGE_CONFIG, "RERANK_CONCURRENCY", cap
    )
    reset_rerank_semaphore_state()

    state = {"in_flight": 0, "peak": 0}

    async def fake_post_json_with_retries(_client, _request, _retry):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.02)
        state["in_flight"] -= 1
        return {"rank_result": [{"id": "x", "score": 1.0}]}

    monkeypatch.setattr(retrieval_mod, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        retrieval_mod, "post_json_with_retries", fake_post_json_with_retries
    )

    async def one() -> None:
        await _rerank_batch(
            cast(AsyncClient, None),
            _RerankBatchRequest(
                user_query="q",
                docs_batch=[{"id": "x", "title": "t", "content": "c"}],
                rerank_url="http://rerank.invalid/rank",
                top_n=1,
                timeout=1.0,
                max_retries=0,
                retriable_codes=(),
            ),
        )

    await asyncio.gather(*(one() for _ in range(calls)))
    return state["peak"]


async def test_rerank_batch_caps_concurrency_at_config_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_rerank_batch`` never exceeds RERANK_CONCURRENCY in flight.

    Patches the config cap to 4 and the HTTP egress to a slow stub that
    records the live in-flight count, then fires 20 batches at once and
    asserts the observed peak never crossed the cap.
    """
    peak = await _fire_rerank_fan_out(monkeypatch, cap=4, calls=20)

    assert peak <= 4, f"peak {peak} exceeded cap 4"
    assert peak >= 2, "stub never overlapped; test is vacuous"


async def test_rerank_batch_full_fan_out_when_cap_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cap of 0 means unthrottled: the peak equals the full fan-out.

    The sentinel test below proves the bypass mechanism (a nullcontext
    is handed out); this pins the promised behavior itself — with the
    cap disabled, 20 concurrent ``_rerank_batch`` calls are all in
    flight at once, so no residual throttle sits on the egress path.
    """
    peak = await _fire_rerank_fan_out(monkeypatch, cap=0, calls=20)

    assert peak == 20, f"peak {peak} != 20; disabled path is throttled"


def test_rerank_semaphore_is_per_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each event loop gets its own semaphore with no cross-loop error.

    A module-level singleton would raise ``RuntimeError: bound to a
    different event loop`` on the second loop; the per-loop registry
    must hand each loop a distinct instance.
    """
    monkeypatch.setattr(
        retrieval_mod.KNOWLEDGE_CONFIG, "RERANK_CONCURRENCY", 4
    )
    reset_rerank_semaphore_state()

    seen: list[asyncio.Semaphore] = []

    async def grab() -> None:
        sem = _rerank_semaphore()
        async with sem:
            seen.append(sem)

    def run_in_fresh_loop() -> None:
        """Run one probe on an explicitly closed event loop."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(grab())
        finally:
            loop.close()

    run_in_fresh_loop()
    run_in_fresh_loop()

    assert len(seen) == 2
    assert seen[0] is not seen[1], "two loops shared one semaphore instance"


async def test_rerank_semaphore_bypasses_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-positive cap returns a nullcontext (throttling disabled)."""
    monkeypatch.setattr(
        retrieval_mod.KNOWLEDGE_CONFIG, "RERANK_CONCURRENCY", 0
    )
    reset_rerank_semaphore_state()

    ctx = _rerank_semaphore()

    assert not isinstance(ctx, asyncio.Semaphore)
    async with ctx:
        pass
    assert rerank_semaphore_state_size() == 0


def test_rerank_concurrency_is_not_a_per_call_override() -> None:
    """RERANK_CONCURRENCY stays a deployment-level knob only.

    A per-loop singleton semaphore can only honor the first caller's
    value, so a per-call override would silently no-op for later
    callers. Pin the field out of the wrapper override map and the
    per-call ``RerankOptions`` surface so an accidental future wiring
    fails here instead of shipping that trap.
    """
    assert "rerank_concurrency" not in RETRIEVAL_CONFIG_FIELD_MAP
    assert "RERANK_CONCURRENCY" not in RETRIEVAL_CONFIG_FIELD_MAP.values()
    assert "rerank_concurrency" not in {
        field.name for field in fields(RerankOptions)
    }
