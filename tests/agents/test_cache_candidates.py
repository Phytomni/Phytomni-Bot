# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for first-wave low-risk cache integration points."""

import json
from types import SimpleNamespace

import pytest

from mcp_server_phytomni import (
    brief_gene_agents,
    deep_genome_agents,
    knowledge_agents,
    knowledge_retrieval,
)
from mcp_server_phytomni.analyst_agents import get_data_list
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.deep_genome_agents import network_to_string

pytestmark = pytest.mark.agent


def test_get_data_list_tracks_config_file_changes(tmp_path):
    """Verify get data list tracks config file changes."""
    data_file = tmp_path / "species_data.json"
    data_file.write_text(
        json.dumps({"analysis": {"ath": ["first"]}}),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == ["first"]

    data_file.write_text(
        json.dumps({"analysis": {"ath": ["second", "third"]}}),
        encoding="utf-8",
    )

    assert get_data_list(str(data_file), "analysis", "ath") == [
        "second",
        "third",
    ]


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


async def test_retrieve_uses_short_ttl_cache(monkeypatch):
    """Verify retrieve uses short ttl cache."""
    retrieve_cache_clear = getattr(knowledge_agents.retrieve, "cache_clear")
    retrieve_cache_clear()
    calls = {"post": 0, "rerank": 0}

    class FakeResponse:
        """Minimal retrieve response stub."""

        def raise_for_status(self):
            """Verify raise for status."""
            return None

        def json(self):
            """Verify json."""
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
        """Minimal async HTTP client stub."""

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
            """Verify post."""
            del args, kwargs
            calls["post"] += 1
            return FakeResponse()

    async def fake_rerank(**kwargs):
        """Verify fake rerank."""
        del kwargs
        calls["rerank"] += 1
        return [{"chunk_id": "doc-1", "score": 0.9}]

    monkeypatch.setattr(knowledge_retrieval, "AsyncClient", FakeClient)
    monkeypatch.setattr(knowledge_retrieval, "rerank", fake_rerank)

    first = await knowledge_agents.retrieve(
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
    second = await knowledge_agents.retrieve(
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
    assert calls == {"post": 1, "rerank": 1}


async def test_multi_retrieve_uses_short_ttl_cache(monkeypatch):
    """Verify multi retrieve uses short ttl cache."""
    multi_retrieve_cache_clear = getattr(
        knowledge_agents.multi_retrieve,
        "cache_clear",
    )
    multi_retrieve_cache_clear()
    calls = {"retrieve": 0}

    async def fake_retrieve(**kwargs):
        """Verify fake retrieve."""
        calls["retrieve"] += 1
        repo_id = kwargs["repo_id"]
        return {
            "doc_list": [{"chunk_id": repo_id, "score": 0.8}],
            "total": 10000,
        }

    monkeypatch.setattr(knowledge_retrieval, "retrieve", fake_retrieve)

    first = await knowledge_agents.multi_retrieve(
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
    second = await knowledge_agents.multi_retrieve(
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
    assert calls["retrieve"] == 2


async def test_gene_retrieve_uses_agent_context_cache():
    """Verify gene retrieve uses agent context cache."""
    brief_gene_agents.clear_gene_retrieve_cache()
    calls = {"arun": 0}

    class FakeKnowledgeAgent:
        """Minimal KnowledgeAgent-compatible stub."""

        knowledge_config = KnowledgeConfig()

        async def arun(self, **kwargs):
            """Verify arun."""
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
            """Return the fake knowledge configuration."""
            return self.knowledge_config

    first = await brief_gene_agents.gene_retrieve(
        "Arabidopsis thaliana",
        ["NAC001", "NAC001"],
        FakeKnowledgeAgent(),
        top_n=1,
    )
    second = await brief_gene_agents.gene_retrieve(
        "Arabidopsis thaliana",
        ["NAC001", "NAC001"],
        FakeKnowledgeAgent(),
        top_n=1,
    )

    assert first == second
    assert calls["arun"] == 1


async def test_deep_genome_gene_symbol_lookup_uses_cache(monkeypatch):
    """Verify deep genome gene symbol lookup uses cache."""
    deep_genome_agents.clear_gene_lookup_caches()
    calls = {"post": 0}

    def fake_post(*args, **kwargs):
        """Verify fake post."""
        del args, kwargs
        calls["post"] += 1
        return SimpleNamespace(
            json=lambda: {"data": [{"symbol": "NAC001|NAC002"}]}
        )

    monkeypatch.setattr(deep_genome_agents.requests, "post", fake_post)

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
    """Verify deep genome gene annotation lookup uses cache."""
    deep_genome_agents.clear_gene_lookup_caches()
    calls = {"post": 0}

    def fake_post(*args, **kwargs):
        """Verify fake post."""
        del args
        calls["post"] += 1
        sql = kwargs["json"]["sql"]
        if "description" in sql:
            payload = {"data": [{"description": "NAC factor"}]}
        elif "ontology" in sql:
            payload = {"data": [{"go_id": "GO:1", "go_name": "binding"}]}
        elif "interpro" in sql:
            payload = {"data": [{"interpro_id": "IPR1"}]}
        else:
            payload = {"data": [{"mapman": "27.3"}]}
        return SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(deep_genome_agents.requests, "post", fake_post)

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
