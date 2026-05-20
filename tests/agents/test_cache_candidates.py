# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for first-wave low-risk cache integration points.

Covers config-file cache invalidation, retrieval TTL caches, gene literature
cache keys, and DeepGenome BI lookup cache behavior.
"""

import json
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


def test_network_to_string_uses_cache_for_identical_inputs():
    """Verify network to string uses cache for identical inputs."""
    network_to_string.cache_clear()

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
    assert network_to_string.cache_info() == {
        "hits": 1,
        "misses": 1,
        "count": 1,
    }


async def test_retrieve_uses_primitive_scope_cache(monkeypatch):
    """Verify retrieve dedupes the retrieve-scope HTTP via the primitive cache.

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

    monkeypatch.setattr(knowledge_retrieval, "AsyncClient", FakeClient)
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
    # Primitive _retrieve_scope_docs cache catches the second retrieve
    # roundtrip, so post stays at 1; the composite cache is gone so the
    # module-level rerank stub is invoked once per retrieve() call.
    assert calls == {"post": 1, "rerank": 2}


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

    monkeypatch.setattr(knowledge_retrieval, "AsyncClient", FakeClient)
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
    # Two repos × one retrieve HTTP each = 2 primitive cache misses on the
    # first multi_retrieve; the second multi_retrieve hits the cache for
    # both repos, so post stays at 2. The composite is gone, so the
    # stubbed rerank is invoked once per repo per multi_retrieve.
    assert calls == {"post": 2, "rerank": 4}


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


async def test_deep_genome_gene_symbol_lookup_uses_cache(monkeypatch):
    """Verify deep genome gene symbol lookup uses cache.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace requests.post.

    Returns:
        None after repeated lookup shares one BI request.
    """
    deep_genome_agents.clear_gene_lookup_caches()
    calls = {"post": 0}

    async def fake_helper(*args, **kwargs):
        """Return fake symbol rows from the BI retry helper.

        Args:
            *args: Ignored client/request/retry positional arguments.
            **kwargs: Ignored keyword arguments.

        Returns:
            Decoded BI payload with symbol rows.
        """
        del args, kwargs
        calls["post"] += 1
        return {"data": [{"symbol": "NAC001|NAC002"}]}

    monkeypatch.setattr(
        deep_genome_profile, "post_json_with_retries", fake_helper
    )

    lookup_symbol = getattr(deep_genome_agents, "_cached_gene_symbol_lookup")
    first = await lookup_symbol(
        bi_url="https://example.invalid/bi",
        sql_headers={"token": "secret-one"},
        species_code="ath",
        gene_id="AT1G01010",
    )
    second = await lookup_symbol(
        bi_url="https://example.invalid/bi",
        sql_headers={"token": "secret-two"},
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert set(first) == {"NAC001", "NAC002"}
    assert set(second) == {"NAC001", "NAC002"}
    assert calls["post"] == 1


async def test_deep_genome_gene_annotation_lookup_uses_cache(monkeypatch):
    """Verify deep genome gene annotation lookup uses cache.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace requests.post.

    Returns:
        None after repeated lookup reuses cached annotation rows.
    """
    deep_genome_agents.clear_gene_lookup_caches()
    calls = {"post": 0}

    async def fake_helper(client, request, retry):
        """Return fake annotation rows selected by SQL text.

        Args:
            client: Ignored async HTTP client.
            request: BI request whose json_body carries the SQL.
            retry: Ignored retry policy.

        Returns:
            Decoded BI payload with annotation rows.
        """
        del client, retry
        calls["post"] += 1
        sql = request.json_body["sql"]
        if "description" in sql:
            payload = {"data": [{"description": "NAC factor"}]}
        elif "ontology" in sql:
            payload = {"data": [{"go_id": "GO:1", "go_name": "binding"}]}
        elif "interpro" in sql:
            payload = {"data": [{"interpro_id": "IPR1"}]}
        else:
            payload = {"data": [{"mapman": "27.3"}]}
        return payload

    monkeypatch.setattr(
        deep_genome_profile, "post_json_with_retries", fake_helper
    )

    lookup_annotation = getattr(
        deep_genome_agents,
        "_cached_gene_annotation_lookup",
    )
    first = await lookup_annotation(
        bi_url="https://example.invalid/bi",
        sql_headers={"token": "secret-one"},
        species_code="ath",
        gene_id="AT1G01010",
    )
    second = await lookup_annotation(
        bi_url="https://example.invalid/bi",
        sql_headers={"token": "secret-two"},
        species_code="ath",
        gene_id="AT1G01010",
    )

    assert first == second
    assert set(first) == {"description", "go", "interpro", "mapman"}
    assert calls["post"] == 4
