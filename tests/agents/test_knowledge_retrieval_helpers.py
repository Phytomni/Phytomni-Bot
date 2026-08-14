# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the knowledge retrieval pure helpers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge import retrieval as retrieval_mod
from mcp_server_phytomni.agents.knowledge.retrieval import (
    RerankOptions,
    _collect_rank_results,
    _list_or_empty,
    _rerank_batch,
    _rerank_docs,
    _RerankBatchRequest,
    _retrieve_cached,
    _retrieve_scope_docs,
    _sorted_merged_docs,
    clear_retrieval_caches,
)
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RerankOptions as LeafRerankOptions,
)
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RetrieveOptions,
)
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RETRIEVAL_UNAVAILABLE_MESSAGE,
)
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from tests.support.outbound_fakes import (
    RecordingResources,
    assert_started_pool_attempts,
    bounded_await,
    bounded_wait_for_event,
    managed_async_task,
    recording_outbound_runtime,
)

pytestmark = pytest.mark.unit


class _KnowledgeBoundaryTransport(httpx.AsyncBaseTransport):
    """Script the real retrieve/rerank adapters at their HTTP boundary."""

    def __init__(
        self,
        *,
        blocked_service: str | None = None,
        entered_target: int = 1,
    ) -> None:
        self.blocked_service = blocked_service
        self.entered_target = entered_target
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.operations: list[str] = []
        self.active = 0
        self.max_active = 0

    @staticmethod
    def _service(request: httpx.Request) -> str:
        """Classify the two fixed synthetic upstream URLs."""
        return "rerank" if request.url.host == "rerank.test" else "retrieval"

    @staticmethod
    def _response_payload(
        service: str, request: httpx.Request
    ) -> dict[str, Any]:
        """Build the smallest valid provider response for one request."""
        body = json.loads(request.content)
        if service == "retrieval":
            suffix = f"{body['repo_id']}-{body['scope']}"
            return {
                "doc_list": [
                    {
                        "chunk_id": suffix,
                        "title": f"title-{suffix}",
                        "content": f"content-{suffix}",
                    }
                ]
            }
        return {
            "rank_result": [
                {"id": doc["id"], "score": 1.0} for doc in body["docs"]
            ]
        }

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        """Record, optionally block, and answer one outer HTTP request."""
        service = self._service(request)
        self.operations.append(service)
        if service == self.blocked_service:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self.entered_target:
                self.entered.set()
            try:
                await self.release.wait()
            finally:
                self.active -= 1
        payload = self._response_payload(service, request)
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=request,
        )


async def _wait_for_pool_waiters(
    runtime: Any,
    pool: OutboundPoolName,
    expected: int,
    task: asyncio.Task[Any],
) -> None:
    """Wait boundedly for a real public adapter to reach pool capacity."""

    async def wait() -> None:
        while runtime.pools.snapshot(pool).waiting != expected:
            if task.done():
                await task
                raise AssertionError(
                    "adapter completed before pool saturation"
                )
            await asyncio.sleep(0)

    await bounded_await(wait())


def _knowledge_call_options() -> dict[str, Any]:
    """Return deterministic direct-upstream options for public adapters."""
    return {
        "retrieve_url": "https://retrieve.test/search",
        "rerank_url": "https://rerank.test/rank",
        "timeout": 1.0,
        "max_retries": 0,
        "retriable_codes": [],
        "score_threshold": 0.0,
    }


def _rerank_call_options() -> dict[str, Any]:
    """Return the rerank-only subset accepted by the public boundary."""
    options = _knowledge_call_options()
    options.pop("retrieve_url")
    return options


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

    assert excinfo.value.error.message == RETRIEVAL_UNAVAILABLE_MESSAGE


def test_collect_rank_results_re_raises_cancellation() -> None:
    """Cancellation from one rerank batch retains task-control semantics."""
    cancelled = asyncio.CancelledError("cancel")

    with pytest.raises(asyncio.CancelledError) as excinfo:
        _collect_rank_results([cancelled], top_n=10)

    assert excinfo.value is cancelled


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
    ]

    docs, id_doc_dict = _rerank_docs(doc_list)

    assert [doc["id"] for doc in docs] == ["c1", "c2"]
    assert docs[0]["content"] == "BIG"
    assert docs[1]["content"] == "fallback"
    assert "c1" in id_doc_dict


def test_rerank_docs_rejects_missing_content() -> None:
    """Malformed source documents fail instead of disappearing silently."""
    with pytest.raises(ValueError, match="Invalid retrieval response"):
        _rerank_docs([{"chunk_id": "c3", "title": "Paper C"}])


def test_sorted_merged_docs_is_stable_and_trims_to_top_n() -> None:
    """Sort finite scores first and preserve source order for ties/misses."""
    results: list[Any] = [
        {
            "doc_list": [
                {
                    "chunk_id": "tie-a",
                    "title": "Tie A",
                    "content": "Tie A",
                    "score": 0.8,
                },
                {
                    "chunk_id": "missing-a",
                    "title": "Missing A",
                    "content": "Missing A",
                },
            ]
        },
        {
            "doc_list": [
                {
                    "chunk_id": "tie-b",
                    "title": "Tie B",
                    "content": "Tie B",
                    "score": 0.8,
                },
                {
                    "chunk_id": "middle",
                    "title": "Middle",
                    "content": "Middle",
                    "score": 0.5,
                },
                {
                    "chunk_id": "missing-b",
                    "title": "Missing B",
                    "content": "Missing B",
                },
                {
                    "chunk_id": "low",
                    "title": "Low",
                    "content": "Low",
                    "score": 0.1,
                },
            ]
        },
    ]

    sorted_docs = _sorted_merged_docs(results, top_n=5)

    assert [doc["chunk_id"] for doc in sorted_docs] == [
        "tie-a",
        "tie-b",
        "middle",
        "low",
        "missing-a",
    ]


def test_sorted_merged_docs_returns_all_when_top_n_is_zero() -> None:
    """A top_n of 0 or None disables truncation."""
    results: list[Any] = [
        {
            "doc_list": [
                {"chunk_id": "a", "title": "A", "content": "A", "score": 0.2},
                {"chunk_id": "b", "title": "B", "content": "B", "score": 0.4},
            ]
        },
    ]

    assert len(_sorted_merged_docs(results, top_n=0)) == 2


def test_list_or_empty_coerces_none_and_iterables() -> None:
    """``_list_or_empty`` returns lists as-is and coerces other iterables."""
    assert _list_or_empty(None) == []
    assert _list_or_empty([1, 2]) == [1, 2]
    assert _list_or_empty((3, 4)) == [3, 4]


async def test_retrieve_raw_docs_rejects_unknown_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unsupported retrieval scopes before issuing an HTTP request."""
    del monkeypatch
    options = RetrieveOptions.from_kwargs({"scope": "unsupported"})

    with pytest.raises(ValueError, match="Invalid scope value"):
        retrieve_raw_docs = getattr(retrieval_mod, "_retrieve_raw_docs")
        await retrieve_raw_docs("query", options)


def test_clear_retrieval_caches_invokes_surviving_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the scoped and reranked caches remain clearable."""
    calls: list[str] = []
    monkeypatch.setattr(
        _retrieve_cached, "cache_clear", lambda: calls.append("single")
    )
    monkeypatch.setattr(
        _retrieve_scope_docs, "cache_clear", lambda: calls.append("scope")
    )

    clear_retrieval_caches()

    assert calls == ["single", "scope"]


async def test_rerank_batch_has_no_legacy_concurrency_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rerank transport receives its complete request from the batch object."""
    monkeypatch.setattr(retrieval_mod, "KNOWLEDGE_CONFIG", object())

    async def fake_post_json_with_retries(_client, _request, _retry):
        """Return one deterministic ranking without reading global config."""
        return {"rank_result": [{"id": "x", "score": 1.0}]}

    monkeypatch.setattr(retrieval_mod, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        retrieval_mod, "post_json_with_retries", fake_post_json_with_retries
    )

    ranked = await _rerank_batch(
        cast(Any, None),
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

    assert ranked == [{"id": "x", "score": 1.0}]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rank_result": "not-a-list"},
        {"rank_result": [{"id": "unknown", "score": 1.0}]},
        {"rank_result": [{"id": "x", "score": float("inf")}]},
        {"rank_result": [{"id": "x"}]},
    ],
)
async def test_rerank_batch_rejects_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    """Rerank provider responses must match the submitted batch contract."""

    async def fake_post_json_with_retries(_client, _request, _retry):
        return payload

    monkeypatch.setattr(retrieval_mod, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        retrieval_mod, "post_json_with_retries", fake_post_json_with_retries
    )

    with pytest.raises(ValueError, match="Invalid rerank response"):
        await _rerank_batch(
            cast(Any, None),
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


async def test_public_retrieve_uses_retrieval_then_rerank_pools() -> None:
    """A public direct retrieve owns one request in each business pool.

    ``retrieve`` is intentionally a two-operation business path: retrieval
    completes and releases before the returned documents enter reranking.
    """
    transport = _KnowledgeBoundaryTransport()
    resources = RecordingResources(transport=transport)
    clear_retrieval_caches()
    try:
        async with recording_outbound_runtime(
            config=ServerConfig(), resources=resources
        ) as runtime:
            result = await retrieval_mod.retrieve(
                "classification-retrieve",
                repo_id="repo-one",
                page_size=1,
                scope="doc",
                rerank_batch_size=10,
                **_knowledge_call_options(),
            )

            assert result["doc_list"][0]["chunk_id"] == "repo-one-doc"
            assert transport.operations == ["retrieval", "rerank"]
            assert_started_pool_attempts(
                runtime,
                {
                    OutboundPoolName.RETRIEVAL: 1,
                    OutboundPoolName.RERANK: 1,
                },
            )
            retrieval = runtime.pools.snapshot(OutboundPoolName.RETRIEVAL)
            rerank = runtime.pools.snapshot(OutboundPoolName.RERANK)
            assert retrieval.completed == 1
            assert rerank.completed == 1
            assert retrieval.in_use == rerank.in_use == 0
    finally:
        clear_retrieval_caches()


async def test_public_rerank_records_only_one_rerank_operation() -> None:
    """A one-batch public rerank changes only the typed rerank pool."""
    transport = _KnowledgeBoundaryTransport()
    resources = RecordingResources(transport=transport)
    async with recording_outbound_runtime(
        config=ServerConfig(), resources=resources
    ) as runtime:
        result = await retrieval_mod.rerank(
            "classification-rerank",
            [
                {
                    "chunk_id": "chunk-one",
                    "title": "Title one",
                    "content": "Content one",
                }
            ],
            top_n=1,
            rerank_batch_size=10,
            **_rerank_call_options(),
        )

        assert result[0]["chunk_id"] == "chunk-one"
        assert transport.operations == ["rerank"]
        assert_started_pool_attempts(runtime, {OutboundPoolName.RERANK: 1})


async def test_public_retrieve_scope_fan_out_respects_retrieval_capacity() -> (
    None
):
    """Both direct retrieval scopes acquire independently.

    The process capacity of one bounds the two-scope fan-out.
    """
    transport = _KnowledgeBoundaryTransport(blocked_service="retrieval")
    resources = RecordingResources(transport=transport)
    clear_retrieval_caches()
    try:
        config = ServerConfig().model_copy(
            update={"OUTBOUND_RETRIEVAL_CONCURRENCY": 1}
        )
        async with recording_outbound_runtime(
            config=config, resources=resources
        ) as runtime:
            async with managed_async_task(
                retrieval_mod.retrieve(
                    "bounded-retrieval-fan-out",
                    repo_id="repo-fan-out",
                    page_size=2,
                    scope="both",
                    rerank_batch_size=10,
                    **_knowledge_call_options(),
                ),
                release_events=(transport.release,),
            ) as task:
                await bounded_wait_for_event(transport.entered, task=task)
                await _wait_for_pool_waiters(
                    runtime,
                    OutboundPoolName.RETRIEVAL,
                    1,
                    task,
                )
                saturated = runtime.pools.snapshot(OutboundPoolName.RETRIEVAL)
                assert saturated.in_use == 1
                assert saturated.waiting == 1
                assert saturated.max_in_use == 1

                transport.release.set()
                result = await bounded_await(task)

            assert len(result["doc_list"]) == 2
            retrieval = runtime.pools.snapshot(OutboundPoolName.RETRIEVAL)
            assert retrieval.started == 2
            assert retrieval.completed == 2
            assert retrieval.max_in_use == 1
            assert retrieval.in_use == retrieval.waiting == 0
            assert transport.max_active == 1
            assert transport.operations == [
                "retrieval",
                "retrieval",
                "rerank",
            ]
    finally:
        clear_retrieval_caches()


async def test_public_rerank_batch_fan_out_respects_rerank_capacity() -> None:
    """Three public rerank batches peak at the configured capacity of two."""
    transport = _KnowledgeBoundaryTransport(
        blocked_service="rerank", entered_target=2
    )
    resources = RecordingResources(transport=transport)
    docs = [
        {
            "chunk_id": f"chunk-{index}",
            "title": f"Title {index}",
            "content": f"Content {index}",
        }
        for index in range(3)
    ]
    config = ServerConfig().model_copy(
        update={"OUTBOUND_RERANK_CONCURRENCY": 2}
    )
    async with recording_outbound_runtime(
        config=config, resources=resources
    ) as runtime:
        async with managed_async_task(
            retrieval_mod.rerank(
                "bounded-rerank-fan-out",
                docs,
                top_n=3,
                rerank_batch_size=1,
                **_rerank_call_options(),
            ),
            release_events=(transport.release,),
        ) as task:
            await bounded_wait_for_event(transport.entered, task=task)
            await _wait_for_pool_waiters(
                runtime,
                OutboundPoolName.RERANK,
                1,
                task,
            )
            saturated = runtime.pools.snapshot(OutboundPoolName.RERANK)
            assert saturated.in_use == 2
            assert saturated.waiting == 1
            assert saturated.max_in_use == 2

            transport.release.set()
            result = await bounded_await(task)

        assert len(result) == 3
        rerank = runtime.pools.snapshot(OutboundPoolName.RERANK)
        assert rerank.started == 3
        assert rerank.completed == 3
        assert rerank.max_in_use == 2
        assert rerank.in_use == rerank.waiting == 0
        assert transport.max_active == 2
        assert transport.operations == ["rerank", "rerank", "rerank"]
