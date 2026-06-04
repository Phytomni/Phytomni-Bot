# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for brief_gene 4-parallel section LLM nodes.

Each section node calls ``phyto_chat`` with its dedicated prompt
(``brief_gene_section_discovery`` / ``_cloning`` / ``_functional`` /
``_application``), projects the LLM output into the matching state
markdown field (``section1_markdown`` through ``section4_markdown``),
and increments ``gene_profile_completed_branches`` so the downstream
barrier can advance.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.agents.brief_gene.analytical_sections import (
    _run_section1_node,
    _run_section2_node,
    _run_section3_node,
    _run_section4_node,
)

pytestmark = pytest.mark.agent


def _state() -> dict[str, Any]:
    """Minimal happy-path state with annotations + lit context."""
    return {
        "gene_id": "Os01g0177400",
        "species_code": "osa",
        "species_english_name": "rice",
        "gene_name_symbol_list": ["OsCAB1"],
        "description_string": "chlorophyll a/b-binding protein",
        "go_string": "GO:0009522",
        "kegg_string": "osa:00196",
        "interpro_string": "IPR001344",
        "gene_structure_string": "5 exons; 4 introns",
        "orthologs_data": {"gene_list": [{"homology_gene_id": "AT1G29910"}]},
        "paralogs_data": {"gene_list": []},
        "interaction_data": {"gene_list": []},
        "ortholog_count": 1,
        "ortholog_species_count": 1,
        "paralog_count": 0,
        "interaction_count": 0,
        "retrieve_context": "[document:1] CAB1 expression in rice ...",
        "retrieved_docs": [{"id": "doc1"}],
    }


def _mock_chat_response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


@pytest.mark.asyncio
async def test_section1_node_writes_markdown_and_barrier() -> None:
    """§1 Discovery node writes section1_markdown + barrier += 1."""
    mock_chat = AsyncMock(
        return_value=_mock_chat_response("### 1. Gene Discovery\n\nContent.")
    )
    with patch(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_section1_node(cast(Any, _state()))

    assert delta["section1_markdown"].startswith("### 1. Gene Discovery")
    assert delta["gene_profile_completed_branches"] == 1


@pytest.mark.asyncio
async def test_section2_node_writes_markdown_and_barrier() -> None:
    """§2 Cloning node writes section2_markdown + barrier += 1."""
    mock_chat = AsyncMock(
        return_value=_mock_chat_response("### 2. Gene Cloning\n\nContent.")
    )
    with patch(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_section2_node(cast(Any, _state()))

    assert delta["section2_markdown"].startswith("### 2. Gene Cloning")
    assert delta["gene_profile_completed_branches"] == 1


@pytest.mark.asyncio
async def test_section3_node_writes_markdown_and_barrier() -> None:
    """§3 Functional node writes section3_markdown + barrier += 1."""
    mock_chat = AsyncMock(
        return_value=_mock_chat_response(
            "### 3. Functional Analysis\n\nContent."
        )
    )
    with patch(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_section3_node(cast(Any, _state()))

    assert delta["section3_markdown"].startswith("### 3. Functional Analysis")
    assert delta["gene_profile_completed_branches"] == 1


@pytest.mark.asyncio
async def test_section4_node_writes_markdown_and_barrier() -> None:
    """§4 Application node writes section4_markdown + barrier += 1."""
    mock_chat = AsyncMock(
        return_value=_mock_chat_response(
            "### 4. Application and Evolutionary\n\nContent."
        )
    )
    with patch(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections.phyto_chat",
        new=mock_chat,
    ):
        delta = await _run_section4_node(cast(Any, _state()))

    assert delta["section4_markdown"].startswith(
        "### 4. Application and Evolutionary"
    )
    assert delta["gene_profile_completed_branches"] == 1


@pytest.mark.asyncio
async def test_section_nodes_pass_homology_context_into_prompt() -> None:
    """Each section LLM receives a homology_context summary string.

    The shared ``_build_section_context`` helper composes ortholog /
    paralog / interaction counts into a single ``homology_context``
    template variable so the section LLM can reference cross-species
    or interaction patterns in its subsections (e.g. §4.2 Genetic
    Diversity or §3.3 Networks).
    """
    mock_chat = AsyncMock(return_value=_mock_chat_response("X"))
    with patch(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections.phyto_chat",
        new=mock_chat,
    ):
        await _run_section1_node(cast(Any, _state()))

    rendered_query = mock_chat.call_args.kwargs.get("user_query", "")
    # The homology_context line should mention the ortholog count
    assert "Orthologs: 1" in rendered_query
    assert "Paralogs: 0" in rendered_query
    assert "interactions: 0" in rendered_query
