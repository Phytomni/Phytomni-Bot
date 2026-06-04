# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end test for brief_gene preamble workflow (legacy wire).

Verifies that the post-M10 ``_wire_legacy`` topology runs the
4-section fan-out + barrier + introduction + render in correct
order. Uses mocked LLM + mocked BI to keep the test offline.
Covers happy (``gene_found=True``) and degraded
(``gene_found=False``) paths.

USE_CHAT_SUBGRAPH defaults to False so this test exercises the
legacy wire (which M10 updated). The chat-subgraph wire is left
unchanged in M10; a follow-up commit will mirror the preamble
fan-out into ``_wire_chat_subgraph``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent

pytestmark = pytest.mark.agent


def _mock_chat_response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


def _bi_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap rows in BI shape (``message: ok`` + ``data``).

    The ``_response_data`` helper in pipeline.py rejects responses
    that do not carry ``message="ok"`` (treats them as failure and
    returns an empty list), so test fixtures must include that key
    to trigger downstream node logic correctly.
    """
    return {"message": "ok", "data": rows}


def _query_judge_response(gene_id: str = "Os01g0177400") -> dict[str, Any]:
    """First id2multispecies response when gene_found=True."""
    return _bi_response(
        [
            {
                "gene_id": gene_id,
                "species_code": "osa",
                "id_type": "rapdb",
            }
        ]
    )


def _gene_id_info_response() -> dict[str, Any]:
    """Second id2multispecies response (gene_id_info)."""
    return _bi_response([{"id_type": "msu7", "species_code": "osa"}])


def _species_response() -> dict[str, Any]:
    return _bi_response(
        [
            {
                "species_scientific_name": "Oryza sativa",
                "species_name_eng": "rice",
            }
        ]
    )


def _annotation_response_factory(payload: dict[str, Any]) -> dict[str, Any]:
    return _bi_response([payload])


def _homology_response() -> dict[str, Any]:
    return _bi_response(
        [
            {
                "query_gene_id": "Os01g0177400",
                "query_species": "osa",
                "homology_gene_id": "AT1G29910",
                "homology_species": "ath",
            }
        ]
    )


def _interaction_response() -> dict[str, Any]:
    return _bi_response([])


@pytest.mark.asyncio
async def test_workflow_happy_path_assembles_full_preamble() -> None:
    """gene_found=True: 4 sections + intro + render produce preamble.

    final_response.content must include title + intro +
    ## Gene Profiles + ### Basic Genomic Information bullets +
    section1-4 markdowns.
    """
    agent = BriefGeneAgent()

    # query_judge_node issues 3 BI calls; fetch_annotation_node issues 6.
    bi_responses = [
        _query_judge_response(),  # first id2multispecies
        _gene_id_info_response(),  # second id2multispecies
        _species_response(),  # species
        _annotation_response_factory(
            {
                "symbol": "OsCAB1",
                "chromosome": "1",
                "start": "1000000",
                "end": "1003000",
                "strand": "+",
            }
        ),  # id_table (annotation 0)
        _annotation_response_factory(
            {"gene_length": "850", "exon_number": 5}
        ),  # annotation_gene_structure_col (1)
        _annotation_response_factory(
            {"go_id": "GO:0009522", "go_name": "photosystem I"}
        ),  # annotation_gene_ontology (2)
        _annotation_response_factory(
            {"mapman_id": "osa:00196", "mapman_name": "Photosynthesis"}
        ),  # annotation_gene_mapman (3)
        _annotation_response_factory(
            {"interpro_id": "IPR001344", "interpro_name": "CAB"}
        ),  # annotation_gene_interpro (4)
        _annotation_response_factory(
            {"description": "chlorophyll a/b-binding protein"}
        ),  # annotation_gene_description (5)
    ]
    chat_sequence = [
        _mock_chat_response("Section 1 content"),
        _mock_chat_response("Section 2 content"),
        _mock_chat_response("Section 3 content"),
        _mock_chat_response("Section 4 content"),
        _mock_chat_response("Introduction text"),
        _mock_chat_response('["follow1"]'),
    ]

    with (
        patch(
            "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
            new=AsyncMock(side_effect=bi_responses),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.homology.relay_bi_query",
            new=AsyncMock(
                side_effect=[_homology_response(), _interaction_response()]
            ),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.core.gene_retrieve",
            new=AsyncMock(
                return_value={
                    "doc_list": [{"id": "doc1"}],
                    "format_doc_list": "[document:1] CAB1 in rice",
                }
            ),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene"
            ".analytical_sections.phyto_chat",
            new=AsyncMock(side_effect=chat_sequence[:4]),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
            new=AsyncMock(return_value=chat_sequence[4]),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.pipeline.phyto_chat",
            new=AsyncMock(return_value=chat_sequence[5]),
        ),
    ):
        result = await agent.arun(user_query="Os01g0177400")

    content = result["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of Os01g0177400")
    assert "Introduction text" in content
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content
    assert "Section 1 content" in content
    assert "Section 4 content" in content


@pytest.mark.asyncio
async def test_workflow_gene_not_found_degraded_path() -> None:
    """gene_found=False: skip 4 sections, run lit-only intro + render.

    Note ``retrieve_node`` falls through to ``self.ka.arun`` (the
    real KnowledgeAgent) when gene_found=False; the mock patches
    that method so the degraded path never hits the network.
    ``follow_up_node`` also calls ``phyto_chat`` (the
    ``_generate_follow_up`` path) and must be mocked separately.
    """
    agent = BriefGeneAgent()

    with (
        patch(
            "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
            new=AsyncMock(return_value={"data": []}),
        ),
        patch.object(
            agent.ka,
            "arun",
            new=AsyncMock(return_value=[{"id": "doc1"}]),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
            new=AsyncMock(return_value=_mock_chat_response("Degraded intro")),
        ),
        patch(
            "mcp_server_phytomni.agents.brief_gene.pipeline.phyto_chat",
            new=AsyncMock(return_value=_mock_chat_response('["follow up?"]')),
        ),
    ):
        result = await agent.arun(user_query="UnknownGene123")

    content = result["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of UnknownGene123")
    assert "Degraded intro" in content
    assert "*Note: A canonical gene ID could not be resolved" in content
