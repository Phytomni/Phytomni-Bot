# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for cache integration points and idempotence contracts.

Covers config-file change tracking and the retrieval composite caches,
plus the network-formatting and DeepGenome BI lookup helpers whose
function-result caches were removed (now pinned as plain idempotence:
identical inputs return identical results, each call hitting the
backend).
"""

import json
from contextlib import asynccontextmanager
from typing import cast

import pytest

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
from mcp_server_phytomni.agents.shared.analysis_storage import get_data_list
from mcp_server_phytomni.config.defaults import KnowledgeConfig

pytestmark = pytest.mark.agent

# pylint: disable=contextmanager-generator-missing-cleanup
# W0135 is a documented false positive on the canonical
# ``@asynccontextmanager`` + ``async with X() as y: yield y`` pattern.
# Several tests in this file build a scripted ``get_async_client``
# substitute inside the test function body (the fake's class is
# constructed per-test from a scripted behaviour list); the closure
# reference confuses pylint's static analysis even though the
# decorator's ``GeneratorExit`` -> ``__aexit__`` conversion is
# correct. See ``docs/lint-exemptions.md`` for the full analysis.


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


async def test_retrieve_uses_composite_cache(monkeypatch):
    """Verify retrieve memoizes the merged answer per user query.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace HTTP calls.

    Returns:
        None after composite-cache hit/miss assertions pass.
    """
    knowledge_retrieval.clear_retrieval_caches()
    calls = {"post": 0, "rerank": 0}

    class FakeResponse:
        """Minimal retrieve response stub."""

        def raise_for_status(self):
            """No-op successful status check.

            Returns:
                None to indicate success.
            """
            return None

        def json(self):
            """Return a minimal retrieval JSON payload.

            Returns:
                Retrieval response payload with one document.
            """
            return {
                "doc_list": [
                    {
                        "chunk_id": "doc-1",
                        "title": "Leaf",
                        "content": "content",
                    }
                ]
            }

    class FakeClient:
        """Minimal async HTTP client stub.

        Attributes:
            No instance attributes are required; calls are tracked externally.
        """

        def __init__(self, *args, **kwargs):
            """Verify init  ."""
            del args, kwargs

        async def __aenter__(self):
            """Verify aenter  ."""
            return self

        async def __aexit__(self, *args):
            """Verify aexit  ."""
            del args

        async def post(self, *args, **kwargs):
            """Return the fake retrieval response and count the call.

            Args:
                *args: Ignored request positional arguments.
                **kwargs: Ignored request keyword arguments.

            Returns:
                Fake response containing retrieval documents.
            """
            del args, kwargs
            calls["post"] += 1
            return FakeResponse()

    async def fake_rerank(**kwargs):
        """Return a deterministic rerank result.

        Args:
            **kwargs: Ignored rerank request options.

        Returns:
            One scored document entry.
        """
        del kwargs
        calls["rerank"] += 1
        return [{"chunk_id": "doc-1", "score": 0.9}]

    @asynccontextmanager
    async def fake_async_client(**factory_kwargs):
        """Yield the FakeClient as a get_async_client substitute."""
        del factory_kwargs
        async with FakeClient() as opened:
            yield opened

    monkeypatch.setattr(
        knowledge_retrieval, "get_async_client", fake_async_client
    )
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
            "doc_list": [{"chunk_id": "doc-1", "score": 0.9}],
            "total": 10000,
        }
    )
    # The composite _retrieve_cached layer memoizes the merged answer,
    # so the second retrieve(...) returns immediately without touching
    # either the retrieve HTTP or the rerank stub.
    assert calls == {"post": 1, "rerank": 1}


async def test_multi_retrieve_dedupes_via_primitive_cache(monkeypatch):
    """Verify multi_retrieve hits one retrieve HTTP per (repo, query) tuple.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace HTTP calls.

    Returns:
        None after primitive-cache hit/miss assertions pass.
    """
    knowledge_retrieval.clear_retrieval_caches()
    calls = {"post": 0, "rerank": 0}

    class FakeResponse:
        """Minimal retrieve response stub."""

        def raise_for_status(self):
            """No-op successful status check."""
            return None

        def json(self):
            """Return a minimal retrieval JSON payload."""
            return {
                "doc_list": [
                    {
                        "chunk_id": "doc-1",
                        "title": "Leaf",
                        "content": "content",
                    }
                ]
            }

    class FakeClient:
        """Minimal async HTTP client stub for retrieval HTTPs."""

        def __init__(self, *args, **kwargs):
            """Ignore httpx AsyncClient constructor args."""
            del args, kwargs

        async def __aenter__(self):
            """Return self as the async context value."""
            return self

        async def __aexit__(self, *args):
            """Discard async-exit arguments."""
            del args

        async def post(self, *args, **kwargs):
            """Count POST attempts and return a fake retrieval response."""
            del args, kwargs
            calls["post"] += 1
            return FakeResponse()

    async def fake_rerank(**kwargs):
        """Return one ranked doc and count the call."""
        del kwargs
        calls["rerank"] += 1
        return [{"chunk_id": "doc-1", "score": 0.9}]

    @asynccontextmanager
    async def fake_async_client(**factory_kwargs):
        """Yield the FakeClient as a get_async_client substitute."""
        del factory_kwargs
        async with FakeClient() as opened:
            yield opened

    monkeypatch.setattr(
        knowledge_retrieval, "get_async_client", fake_async_client
    )
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
    # The composite _multi_retrieve cache memoizes the merged answer for
    # the (user_query, repo_items, top_n) tuple. First call: two repos
    # each hit the retrieve HTTP once and rerank once. Second call hits
    # the composite cache, so neither retrieve nor rerank is reached.
    assert calls == {"post": 2, "rerank": 2}


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
    brief_gene_agents.clear_gene_retrieve_cache()
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
            return {
                "doc_list": [
                    {
                        "chunk_id": symbol,
                        "title": symbol,
                        "content": "gene content",
                        "score": 0.7,
                    }
                ]
            }

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
