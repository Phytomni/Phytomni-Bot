# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for brief_gene/pipeline.py async helpers.

Pins the pipeline-level helpers that the BriefGeneAgent nodes call:
``gene_retrieve`` early return on empty symbols, ``_gene_retrieve``
semaphore + merge semantics, and ``_generate_follow_up`` prompt
construction. The pure sync helpers
(_response_data, _format_docs, annotation formatters) are covered by
``test_brief_gene_helpers.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.brief_gene.pipeline import (
    GeneRetrieveRequest,
    _gene_retrieve,
    _generate_follow_up,
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

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs["user_query"])
        return []

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

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        return [{"score": 0.9}, {"score": 0.8}]

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    result = await _gene_retrieve(
        request=request,
        knowledge_agent=mock_ka,
        semaphore=semaphore,
    )

    assert result["doc_list"][0]["score"] == 0.9
    assert len(result["doc_list"]) <= 5


async def test_gene_retrieve_keeps_valid_empty_results_as_no_match() -> None:
    """All valid empty child lists remain a successful no-match result."""

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        return []

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    result = await _gene_retrieve(
        GeneRetrieveRequest("Rice", ("LOC1",), 5),
        mock_ka,
    )

    assert result == {"doc_list": [], "total": 10000}


async def test_gene_retrieve_keeps_docs_when_one_child_fails() -> None:
    """Reliable documents survive a partial child failure."""

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        symbol = kwargs["user_query"].splitlines()[-1]
        if kwargs["user_query"] == "Rice\nLOC2":
            raise RuntimeError("private upstream detail")
        return [{"chunk_id": symbol, "title": symbol, "content": "doc"}]

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    result = await _gene_retrieve(
        GeneRetrieveRequest("Rice", ("LOC1", "LOC2"), 5),
        mock_ka,
    )

    assert [doc["chunk_id"] for doc in result["doc_list"]] == [
        "LOC1",
        "LOC2",
    ]


async def test_gene_retrieve_raises_when_all_children_fail() -> None:
    """A failed fan-out with no reliable documents is not a no-match."""

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        raise RuntimeError("private upstream detail")

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await _gene_retrieve(
            GeneRetrieveRequest("Rice", ("LOC1",), 5),
            mock_ka,
        )


async def test_gene_retrieve_propagates_cancellation() -> None:
    """Cancellation is never converted into a retrieval failure."""

    async def fake_arun(**kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        raise asyncio.CancelledError

    mock_ka = AsyncMock()
    mock_ka.arun = fake_arun

    with pytest.raises(asyncio.CancelledError):
        await _gene_retrieve(
            GeneRetrieveRequest("Rice", ("LOC1",), 5),
            mock_ka,
        )


async def test_generate_follow_up_constructs_prompt_and_parses_questions() -> (
    None
):
    """_generate_follow_up builds prompt and returns questions."""
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
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
    # ``setattr`` routes through the dataclass's ``__setattr__`` exactly
    # the same way as an attribute write, so it still raises
    # ``FrozenInstanceError`` (an ``AttributeError`` subclass) at
    # runtime — the dynamic call merely bypasses the static type
    # checkers that would otherwise reject the direct write because the
    # field is frozen, without the need for an inline ``type: ignore``.
    with pytest.raises(AttributeError):
        setattr(request, "species", "Wheat")
