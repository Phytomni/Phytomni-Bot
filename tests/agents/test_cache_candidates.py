# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for cache integration points and idempotence contracts.

Covers config-file change tracking, positive-only retrieval primitives,
and uncached repository merging, plus the network-formatting and
DeepGenome BI lookup helpers whose function-result caches were removed
(now pinned as plain idempotence: identical inputs return identical
results, each call hitting the backend).
"""

import json
from collections.abc import Iterator
from typing import Any, cast

import pytest
from httpx import TimeoutException

from mcp_server_phytomni.agents.brief_gene import agent as brief_gene_agents
from mcp_server_phytomni.agents.deep_genome import agent as deep_genome_agents
from mcp_server_phytomni.agents.deep_genome import (
    profile as deep_genome_profile,
)
from mcp_server_phytomni.agents.deep_genome.formatting import (
    network_to_string,
)
from mcp_server_phytomni.agents.knowledge import (
    retrieval as knowledge_retrieval,
)
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.retrieval import _RetrieveCacheKey
from mcp_server_phytomni.agents.knowledge.retrieval_options import (
    RetrieveOptions,
)
from mcp_server_phytomni.agents.shared.analysis_storage import get_data_list
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.func_cache import func_cache
from mcp_server_phytomni.func_cache.core import CacheRuntime
from mcp_server_phytomni.func_cache.serializer import dumps

pytestmark = pytest.mark.agent


def _cache_runtime(wrapper: Any) -> CacheRuntime:
    """Return the runtime captured by one func_cache wrapper."""
    return next(
        cell.cell_contents
        for cell in wrapper.__closure__ or ()
        if isinstance(cell.cell_contents, CacheRuntime)
    )


def _isolated_cache_wrapper(wrapper: Any, db_path: str) -> Any:
    """Clone one production cache wrapper onto an ephemeral database."""
    runtime = _cache_runtime(wrapper)
    options = runtime.options
    return func_cache(
        key_params=options.key_params,
        db_path=db_path,
        ttl=options.ttl,
        compress=options.compress,
        lock_timeout=options.lock_timeout,
        lock_expire=options.lock_expire,
        exclude_params=options.exclude_params,
        cache_if=options.cache_if,
    )(wrapper.__wrapped__)


@pytest.fixture(name="isolated_retrieval_caches")
def isolated_retrieval_caches_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Iterator[dict[str, Any]]:
    """Route Knowledge cache operations to one test-owned SQLite file."""
    db_path = str(tmp_path / "retrieval-cache.sqlite")
    caches = {
        "retrieve": _isolated_cache_wrapper(
            getattr(knowledge_retrieval, "_retrieve_cached"),
            db_path,
        ),
        "scope": _isolated_cache_wrapper(
            getattr(knowledge_retrieval, "_retrieve_scope_docs"),
            db_path,
        ),
    }
    monkeypatch.setattr(
        knowledge_retrieval,
        "_retrieve_cached",
        caches["retrieve"],
    )
    monkeypatch.setattr(
        knowledge_retrieval,
        "_retrieve_scope_docs",
        caches["scope"],
    )
    yield caches


def test_get_data_list_tracks_config_file_changes(tmp_path):
    """Verify get_data_list tracks config file changes.

    Args:
        tmp_path: Temporary directory used for mutable metadata JSON.
    """
    data_file = tmp_path / "species_data.json"
    data_file.write_text(
        json.dumps({"analysis": {"ath": {"/obs/data/first.fa": "first"}}}),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == {
        "/obs/data/first.fa": "first",
    }

    data_file.write_text(
        json.dumps(
            {
                "analysis": {
                    "ath": {
                        "/obs/data/second.fa": "second",
                        "/obs/data/third.fa": "third",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == {
        "/obs/data/second.fa": "second",
        "/obs/data/third.fa": "third",
    }


def test_network_to_string_is_deterministic_for_identical_inputs():
    """Verify network_to_string returns identical text for identical inputs.

    The function-result cache was removed; this pins the surviving
    contract that the pure formatter is deterministic, so two identical
    invocations still produce the same report text.
    """
    gene_network_list = [("ath", "AT1G01010")]
    species_gene_symbol_dict = {("ath", "AT1G01010"): ["NAC001"]}
    species_gene_anno_dict = {
        ("ath", "AT1G01010"): {
            "description": "NAC domain transcription factor",
            "go": [{"go_id": "GO:0006355", "go_name": "regulation"}],
            "interpro": [
                {
                    "interpro_id": "IPR003441",
                    "interpro_name": "NAC domain",
                }
            ],
            "mapman": [
                {
                    "mapman": "27.3",
                    "mapman_description": "RNA regulation",
                }
            ],
        }
    }

    first = network_to_string(
        gene_network_list,
        species_gene_symbol_dict,
        species_gene_anno_dict,
        "Orthologous",
        top_n=1,
    )
    second = network_to_string(
        gene_network_list,
        species_gene_symbol_dict,
        species_gene_anno_dict,
        "Orthologous",
        top_n=1,
    )

    assert first == second
    assert "NAC domain transcription factor" in first


async def test_retrieve_caches_complete_non_empty_result(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    isolated_retrieval_caches: dict[str, Any],
):
    """Verify a complete non-empty direct result is cacheable.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace HTTP calls.
        outbound_runtime: Recording process-owned outbound runtime.

    Returns:
        None after primitive-cache hit/miss assertions pass.
    """
    knowledge_retrieval.clear_retrieval_caches()
    assert isolated_retrieval_caches["retrieve"].cache_info()["count"] == 0
    calls = {"rerank": 0}
    outbound_runtime.transport.enqueue(
        content=(
            b'{"doc_list":[{"chunk_id":"doc-1","title":"Leaf",'
            b'"content":"content"}]}'
        )
    )

    async def fake_rerank(**kwargs):
        """Return a deterministic rerank result.

        Args:
            **kwargs: Ignored rerank request options.

        Returns:
            One scored document entry.
        """
        del kwargs
        calls["rerank"] += 1
        return [
            {
                "chunk_id": "doc-1",
                "title": "Leaf",
                "content": "content",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)

    first = await knowledge_retrieval.retrieve(
        user_query="leaf growth",
        retrieve_url="https://example.invalid/retrieve",
        repo_id="repo",
        page_num=1,
        page_size=2,
        filter_string=None,
        scope="doc",
        extra_repo_ids=None,
        rerank_url="https://example.invalid/rerank",
        rerank_batch_size=2,
        score_threshold=0.2,
    )
    second = await knowledge_retrieval.retrieve(
        user_query="leaf growth",
        retrieve_url="https://example.invalid/retrieve",
        repo_id="repo",
        page_num=1,
        page_size=2,
        filter_string=None,
        scope="doc",
        extra_repo_ids=None,
        rerank_url="https://example.invalid/rerank",
        rerank_batch_size=2,
        score_threshold=0.2,
    )

    assert (
        first
        == second
        == {
            "doc_list": [
                {
                    "chunk_id": "doc-1",
                    "title": "Leaf",
                    "content": "content",
                    "score": 0.9,
                }
            ],
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }
    )
    # The complete non-empty direct result is admitted by _retrieve_cached,
    # so the second retrieve(...) does not touch either upstream.
    assert len(outbound_runtime.transport.requests) == 1
    assert calls == {"rerank": 1}


async def test_no_match_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    isolated_retrieval_caches: dict[str, Any],
):
    """A valid empty source is retried instead of becoming a durable miss."""
    knowledge_retrieval.clear_retrieval_caches()
    outbound_runtime.transport.enqueue(content=b'{"doc_list":[]}')
    outbound_runtime.transport.enqueue(
        content=(
            b'{"doc_list":[{"chunk_id":"doc-1","title":"Leaf",'
            b'"content":"content"}]}'
        )
    )
    calls = {"rerank": 0}

    async def fake_rerank(**kwargs):
        del kwargs
        calls["rerank"] += 1
        return [
            {
                "chunk_id": "doc-1",
                "title": "Leaf",
                "content": "content",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)
    kwargs = {
        "retrieve_url": "https://example.invalid/retrieve",
        "repo_id": "repo-no-match",
        "page_num": 1,
        "page_size": 2,
        "filter_string": None,
        "scope": "doc",
        "extra_repo_ids": None,
        "rerank_url": "https://example.invalid/rerank",
        "rerank_batch_size": 2,
        "score_threshold": 0.2,
    }

    first = await knowledge_retrieval.retrieve(
        user_query="no-match-then-recover", **kwargs
    )
    second = await knowledge_retrieval.retrieve(
        user_query="no-match-then-recover", **kwargs
    )

    assert first["outcome"] == "no_match"
    assert first["doc_list"] == []
    assert second["outcome"] == "complete"
    assert len(second["doc_list"]) == 1
    assert isolated_retrieval_caches["retrieve"].cache_info()["count"] == 1
    assert isolated_retrieval_caches["scope"].cache_info()["count"] == 1
    assert len(outbound_runtime.transport.requests) == 2
    assert calls == {"rerank": 1}


async def test_repeated_no_match_stores_no_cache_rows(
    outbound_runtime: Any,
    isolated_retrieval_caches: dict[str, Any],
) -> None:
    """Repeated valid empties stay misses at both retrieval cache layers."""
    knowledge_retrieval.clear_retrieval_caches()
    outbound_runtime.transport.enqueue(content=b'{"doc_list":[]}')
    outbound_runtime.transport.enqueue(content=b'{"doc_list":[]}')
    kwargs = {
        "retrieve_url": "https://example.invalid/retrieve",
        "repo_id": "repo-empty",
        "page_num": 1,
        "page_size": 2,
        "filter_string": None,
        "scope": "doc",
        "extra_repo_ids": None,
        "rerank_url": "https://example.invalid/rerank",
        "rerank_batch_size": 2,
        "score_threshold": 0.2,
    }

    first = await knowledge_retrieval.retrieve("empty-twice", **kwargs)
    second = await knowledge_retrieval.retrieve("empty-twice", **kwargs)

    assert first["outcome"] == second["outcome"] == "no_match"
    assert len(outbound_runtime.transport.requests) == 2
    assert isolated_retrieval_caches["retrieve"].cache_info()["count"] == 0
    assert isolated_retrieval_caches["scope"].cache_info()["count"] == 0


async def test_public_retrieve_does_not_read_legacy_contract_key(
    monkeypatch: pytest.MonkeyPatch,
    isolated_retrieval_caches: dict[str, Any],
) -> None:
    """A public v2 request misses an otherwise identical v1 cache row."""
    calls = 0
    kwargs = {
        "repo_id": "repo-versioned",
        "scope": "doc",
        "page_num": 1,
        "page_size": 1,
        "filter_string": None,
        "extra_repo_ids": None,
        "score_threshold": 0.0,
    }
    options = RetrieveOptions.from_kwargs(kwargs)
    payload = options.payload_options
    scope_key = getattr(knowledge_retrieval, "_retrieve_scope_key")(
        "versioned-query",
        options,
        "doc",
    )
    assert scope_key.contract_version == 2
    legacy_key = _RetrieveCacheKey(
        1,
        "versioned-query",
        payload.repo_id,
        options.scope,
        payload.page_num,
        payload.page_size,
        payload.filter_string,
        tuple(payload.extra_repo_ids or ()),
        options.page_size,
        options.score_threshold,
    )

    async def fake_raw(
        _user_query: str,
        _options: RetrieveOptions,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        docs = [
            {
                "chunk_id": f"doc-{calls}",
                "title": f"Title {calls}",
                "content": f"Content {calls}",
            }
        ]
        return {
            "doc_list": docs,
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_rerank(
        *, doc_list: list[dict[str, Any]], **_kwargs: Any
    ) -> list[dict[str, Any]]:
        return [{**doc_list[0], "score": 0.9}]

    monkeypatch.setattr(knowledge_retrieval, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)
    cache = isolated_retrieval_caches["retrieve"]

    legacy = await cache(legacy_key, options=options)
    current = await knowledge_retrieval.retrieve("versioned-query", **kwargs)
    repeated = await knowledge_retrieval.retrieve("versioned-query", **kwargs)

    assert legacy["doc_list"][0]["chunk_id"] == "doc-1"
    assert current == repeated
    assert current["doc_list"][0]["chunk_id"] == "doc-2"
    assert calls == 2
    assert cache.cache_info()["count"] == 2


async def test_rejected_cached_partial_is_evicted_and_recomputed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_retrieval_caches: dict[str, Any],
) -> None:
    """A historical partial v2 row cannot survive positive-only admission."""
    options = RetrieveOptions.from_kwargs({"page_size": 1})
    key = _RetrieveCacheKey(
        contract_version=2,
        user_query="rejected-hit",
        repo_id=options.payload_options.repo_id,
        scope=options.scope,
        page_num=options.payload_options.page_num,
        page_size=options.payload_options.page_size,
        filter_string=options.payload_options.filter_string,
        extra_repo_ids=tuple(options.payload_options.extra_repo_ids or ()),
        top_n=options.page_size,
        score_threshold=options.score_threshold,
    )
    rejected = {
        "doc_list": [
            {
                "chunk_id": "stale",
                "title": "Stale",
                "content": "Stale partial evidence",
                "score": 0.4,
            }
        ],
        "total": 1,
        "outcome": "partial",
        "failures": [
            {"source": "scope:doc", "kind": "timeout", "retryable": True}
        ],
    }
    cache = isolated_retrieval_caches["retrieve"]
    runtime = _cache_runtime(cache)
    cache_hash = runtime.key_builder.build_key((key,), {"options": options})
    runtime.storage.set(
        runtime.key_builder.func_id,
        cache_hash,
        dumps(rejected, runtime.options.compress),
        runtime.options.ttl,
    )
    calls = 0

    async def fake_raw(
        _user_query: str,
        _options: RetrieveOptions,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "doc_list": [
                {
                    "chunk_id": "fresh",
                    "title": "Fresh",
                    "content": "Fresh evidence",
                }
            ],
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_rerank(
        *, doc_list: list[dict[str, Any]], **_kwargs: Any
    ) -> list[dict[str, Any]]:
        return [{**doc_list[0], "score": 0.9}]

    monkeypatch.setattr(knowledge_retrieval, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)

    result = await cache(key, options=options)

    assert result["outcome"] == "complete"
    assert result["doc_list"][0]["chunk_id"] == "fresh"
    assert calls == 1
    assert cache.cache_info()["count"] == 1


async def test_multi_retrieve_dedupes_via_primitive_cache(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
    isolated_retrieval_caches: dict[str, Any],
):
    """Verify multi_retrieve hits one retrieve HTTP per (repo, query) tuple.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace HTTP calls.
        outbound_runtime: Recording process-owned outbound runtime.

    Returns:
        None after primitive-cache hit/miss assertions pass.
    """
    knowledge_retrieval.clear_retrieval_caches()
    calls = {"rerank": 0}
    response = (
        b'{"doc_list":[{"chunk_id":"doc-1","title":"Leaf",'
        b'"content":"content"}]}'
    )
    outbound_runtime.transport.enqueue(content=response)
    outbound_runtime.transport.enqueue(content=response)

    async def fake_rerank(**kwargs):
        """Return one ranked doc and count the call."""
        del kwargs
        calls["rerank"] += 1
        return [
            {
                "chunk_id": "doc-1",
                "title": "Leaf",
                "content": "content",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)

    first = await knowledge_retrieval.multi_retrieve(
        user_query="root growth",
        retrieve_url="https://example.invalid/retrieve",
        repo_id_dict={"repo-a": 1, "repo-b": 1},
        page_num=1,
        filter_string=None,
        scope="doc",
        extra_repo_ids=None,
        rerank_url="https://example.invalid/rerank",
        rerank_batch_size=2,
        score_threshold=0.2,
        top_n=2,
    )
    second = await knowledge_retrieval.multi_retrieve(
        user_query="root growth",
        retrieve_url="https://example.invalid/retrieve",
        repo_id_dict={"repo-a": 1, "repo-b": 1},
        page_num=1,
        filter_string=None,
        scope="doc",
        extra_repo_ids=None,
        rerank_url="https://example.invalid/rerank",
        rerank_batch_size=2,
        score_threshold=0.2,
        top_n=2,
    )

    assert first == second
    # The final repository merge is recomputed, while each complete
    # non-empty repository leaf is reused by its direct cache.
    assert len(outbound_runtime.transport.requests) == 2
    assert calls == {"rerank": 2}
    assert isolated_retrieval_caches["retrieve"].cache_info()["count"] == 2


async def test_partial_repository_merge_retries_only_failed_leaf(
    monkeypatch: pytest.MonkeyPatch,
    isolated_retrieval_caches: dict[str, Any],
) -> None:
    """Recompute a partial merge while reusing its successful repository."""
    raw_calls = {"repo-a": 0, "repo-b": 0}
    rerank_calls = {"repo-a": 0, "repo-b": 0}

    async def fake_raw(
        _user_query: str,
        options: RetrieveOptions,
    ) -> dict[str, Any]:
        repo_id = options.payload_options.repo_id
        raw_calls[repo_id] += 1
        if repo_id == "repo-b" and raw_calls[repo_id] == 1:
            raise TimeoutException("hidden repository failure")
        docs = [
            {
                "chunk_id": repo_id,
                "title": f"Title {repo_id}",
                "content": f"Content {repo_id}",
            }
        ]
        return {
            "doc_list": docs,
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_rerank(
        *, doc_list: list[dict[str, Any]], **_kwargs: Any
    ) -> list[dict[str, Any]]:
        repo_id = doc_list[0]["chunk_id"]
        rerank_calls[repo_id] += 1
        return [{**doc_list[0], "score": 0.5}]

    monkeypatch.setattr(knowledge_retrieval, "_retrieve_raw_docs", fake_raw)
    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)
    knowledge_retrieval.clear_retrieval_caches()
    kwargs: dict[str, Any] = {
        "repo_id_dict": {"repo-b": 1, "repo-a": 1},
        "scope": "doc",
        "top_n": 2,
        "score_threshold": 0.0,
    }

    first = await knowledge_retrieval.multi_retrieve("recover", **kwargs)
    second = await knowledge_retrieval.multi_retrieve("recover", **kwargs)

    assert first["outcome"] == "partial"
    assert [doc["chunk_id"] for doc in first["doc_list"]] == ["repo-a"]
    assert first["failures"] == [
        {"source": "repo:1", "kind": "timeout", "retryable": True}
    ]
    assert second["outcome"] == "complete"
    assert [doc["chunk_id"] for doc in second["doc_list"]] == [
        "repo-a",
        "repo-b",
    ]
    assert raw_calls == {"repo-a": 1, "repo-b": 2}
    assert rerank_calls == {"repo-a": 1, "repo-b": 1}
    assert isolated_retrieval_caches["retrieve"].cache_info()["count"] == 2


async def test_gene_retrieve_is_idempotent_without_composite_cache():
    """Verify gene_retrieve returns identical docs across repeated calls.

    The brief_gene composite cache that previously caught the second
    invocation is gone; this test now pins the behavior that two
    identical gene_retrieve calls return identical merged-and-trimmed
    doc lists and each call independently delegates to
    KnowledgeAgent.arun for every deduplicated query term, since the
    actual roundtrip de-duplication has moved one layer down into the
    retrieval primitive cache (not exercised here because arun is
    stubbed at the agent level).

    Returns:
        None after idempotence assertions pass.
    """
    knowledge_retrieval.clear_retrieval_caches()
    calls = {"arun": 0}

    class FakeKnowledgeAgent:
        """Minimal KnowledgeAgent-compatible stub."""

        knowledge_config = KnowledgeConfig()

        async def arun(self, **kwargs):
            """Return a document for the requested symbol.

            Args:
                **kwargs: KnowledgeAgent run arguments including user_query.

            Returns:
                Retrieval payload containing one gene document.
            """
            calls["arun"] += 1
            symbol = kwargs["user_query"].splitlines()[-1]
            return [
                {
                    "chunk_id": symbol,
                    "title": symbol,
                    "content": "gene content",
                    "score": 0.7,
                }
            ]

        def config_snapshot(self):
            """Return the fake knowledge configuration.

            Returns:
                KnowledgeConfig used for cache fingerprinting.
            """
            return self.knowledge_config

    first = await brief_gene_agents.gene_retrieve(
        "Arabidopsis thaliana",
        ["NAC001", "NAC001"],
        cast(KnowledgeAgent, FakeKnowledgeAgent()),
        top_n=1,
    )
    second = await brief_gene_agents.gene_retrieve(
        "Arabidopsis thaliana",
        ["NAC001", "NAC001"],
        cast(KnowledgeAgent, FakeKnowledgeAgent()),
        top_n=1,
    )

    assert first == second
    assert first["doc_list"] == [
        {
            "chunk_id": "NAC001",
            "title": "NAC001",
            "content": "gene content",
            "score": 0.7,
        }
    ]
    # No composite cache: each gene_retrieve invokes arun once per
    # deduplicated query term, so two identical calls accumulate two
    # underlying agent invocations.
    assert calls["arun"] == 2


async def test_deep_genome_gene_symbol_lookup_hits_bi_each_call(monkeypatch):
    """Verify each gene symbol lookup hits the BI gateway (cache removed).

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the BI helper.

    Returns:
        None after two identical lookups each issue their own BI request.
    """
    calls = {"post": 0}

    async def fake_helper(*args, **kwargs):
        """Return fake symbol rows from the shared BI query helper.

        Args:
            *args: Ignored sql positional argument.
            **kwargs: Ignored retry keyword argument.

        Returns:
            Decoded BI payload with symbol rows.
        """
        del args, kwargs
        calls["post"] += 1
        return {"data": [{"symbol": "NAC001|NAC002"}]}

    monkeypatch.setattr(deep_genome_profile, "bi_query", fake_helper)

    lookup_symbol = getattr(deep_genome_agents, "_cached_gene_symbol_lookup")
    first = await lookup_symbol(
        species_code="ath",
        gene_id="AT1G01010",
    )
    second = await lookup_symbol(
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert set(first) == {"NAC001", "NAC002"}
    assert set(second) == {"NAC001", "NAC002"}
    # The lookup cache was removed, so two identical lookups each issue
    # their own BI request instead of the second hitting a cache.
    assert calls["post"] == 2


async def test_deep_genome_gene_annotation_lookup_hits_bi_each_call(
    monkeypatch,
):
    """Verify each annotation lookup hits BI four times (cache removed).

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the BI helper.

    Returns:
        None after two identical lookups issue eight BI requests in total.
    """
    calls = {"post": 0}

    async def fake_helper(sql, *, retry):
        """Return fake annotation rows selected by SQL text.

        Args:
            sql: BI SQL statement whose text selects the canned rows.
            retry: Ignored retry policy.

        Returns:
            Decoded BI payload with annotation rows.
        """
        del retry
        calls["post"] += 1
        if "description" in sql:
            payload = {"data": [{"description": "NAC factor"}]}
        elif "ontology" in sql:
            payload = {"data": [{"go_id": "GO:1", "go_name": "binding"}]}
        elif "interpro" in sql:
            payload = {"data": [{"interpro_id": "IPR1"}]}
        else:
            payload = {"data": [{"mapman": "27.3"}]}
        return payload

    monkeypatch.setattr(deep_genome_profile, "bi_query", fake_helper)

    lookup_annotation = getattr(
        deep_genome_agents,
        "_cached_gene_annotation_lookup",
    )
    first = await lookup_annotation(
        species_code="ath",
        gene_id="AT1G01010",
    )
    second = await lookup_annotation(
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert first == second
    assert set(first) == {"description", "go", "interpro", "mapman"}
    # Four BI SELECTs per lookup; the cache was removed, so two identical
    # lookups issue eight requests in total.
    assert calls["post"] == 8
