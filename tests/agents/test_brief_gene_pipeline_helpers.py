# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for brief_gene/pipeline.py async helpers.

Pins the pipeline-level helpers that the BriefGeneAgent nodes call:
``gene_retrieve`` early return on empty symbols, ``_gene_retrieve``
semaphore + merge semantics, ``clear_gene_retrieve_cache`` delegation,
and ``_generate_follow_up`` prompt construction. The pure sync helpers
(_response_data, _format_docs, annotation formatters) are covered by
``test_brief_gene_helpers.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.brief_gene.pipeline import (
    GeneRetrieveRequest,
    _gene_retrieve,
    _generate_follow_up,
    clear_gene_retrieve_cache,
    gene_retrieve,
)

pytestmark = pytest.mark.unit


async def test_gene_retrieve_returns_empty_for_empty_symbol_list() -> None:
    """Empty symbol list short-circuits to a sentinel payload."""
    result = await gene_retrieve(
        species="Rice",
        gene_symbol_list=[],
        knowledge_agent=AsyncMock(),
    )
    assert result == {"doc_list": [], "total": 10000}


async def test_gene_retrieve_dedupes_symbols_before_fanning_out() -> None:
    """Duplicate symbols are collapsed into one retrieval call."""
    calls: list[str] = []

    async def fake_arun(**kwargs: Any) -> Dict[str, Any]:
        calls.append(kwargs["user_query"])
        return {"doc_list": []}

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    await gene_retrieve(
        species="Rice",
        gene_symbol_list=["LOC1", "LOC1", "LOC2"],
        knowledge_agent=mock_ka,
    )

    # Two unique symbols plus the combined "\n"-joined string = 3 calls.
    assert len(calls) == 3
    assert "Rice\nLOC1" in calls
    assert "Rice\nLOC2" in calls


async def test_gene_retrieve_passes_top_n_and_semaphore() -> None:
    """top_n and semaphore overrides flow through to _gene_retrieve."""
    request = GeneRetrieveRequest(
        species="Wheat",
        symbols=("GENE1",),
        top_n=5,
    )
    semaphore = asyncio.Semaphore(2)

    async def fake_arun(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return {"doc_list": [{"score": 0.9}, {"score": 0.8}]}

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    result = await _gene_retrieve(
        request=request,
        knowledge_agent=mock_ka,
        semaphore=semaphore,
    )

    assert result["doc_list"][0]["score"] == 0.9
    assert len(result["doc_list"]) <= 5


def test_clear_gene_retrieve_cache_delegates_to_knowledge_layer() -> None:
    """The shim calls clear_retrieval_caches from the knowledge package."""
    with patch(
        "mcp_server_phytomni.agents.brief_gene.pipeline.clear_retrieval_caches"
    ) as mock_clear:
        clear_gene_retrieve_cache()
        mock_clear.assert_called_once()


async def test_generate_follow_up_constructs_prompt_and_parses_questions() -> (
    None
):
    """_generate_follow_up builds prompt and returns questions."""
    captured: Dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": '["What is the gene structure?", '
                        '"How does it interact?"]',
                    }
                }
            ]
        }

    with patch(
        "mcp_server_phytomni.agents.brief_gene.pipeline.phyto_chat",
        side_effect=fake_phyto_chat,
    ):
        questions = await _generate_follow_up(
            user_query="LOC_Os01g01010",
            phyto_response={"choices": [{"message": {"content": "answer"}}]},
        )

    assert "What is gene function of LOC_Os01g01010" in captured["user_query"]
    assert "system_response" in captured["user_query"]
    assert len(questions) == 2
    assert "gene structure" in questions[0]


async def test_gene_retrieve_request_is_frozen_dataclass() -> None:
    """GeneRetrieveRequest is immutable (frozen=True)."""
    request = GeneRetrieveRequest(species="Rice", symbols=("LOC1",), top_n=10)
    with pytest.raises(AttributeError):
        request.species = "Wheat"  # type: ignore[misc]
