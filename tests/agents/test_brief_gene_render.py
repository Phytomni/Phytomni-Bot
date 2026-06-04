# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for brief_gene preamble render node.

Pins the markdown structure ``_render_preamble_node`` assembles
on both gene_found=True (full preamble) and gene_found=False
(degraded Note-line variant) paths.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.brief_gene.render import (
    _render_preamble_node,
)

pytestmark = pytest.mark.agent


def _full_state() -> dict[str, Any]:
    return {
        "gene_found": True,
        "user_query": "Os01g0177400",
        "query_id_version": "rapdb",
        "gene_id": "Os01g0177400",
        "gene_id_version": "msu7",
        "species_code": "osa",
        "species_english_name": "rice",
        "species_latin_name": "Oryza sativa",
        "gene_name_symbol_list": ["OsCAB1", "CAB1"],
        "gene_id_list": ["Os01g0177400", "LOC_Os01g08000"],
        "cross_species_alias_count": 3,
        "cross_species_alias_species_count": 2,
        "gene_chr": "chr1",
        "gene_start": "1000000",
        "gene_end": "1003000",
        "gene_strand": "+",
        "gene_structure_string": "5 exons, 4 introns, CDS 850 bp",
        "description_string": "chlorophyll a/b-binding protein",
        "go_string": "GO:0009522 photosystem I",
        "kegg_string": "osa:00196 Photosynthesis",
        "interpro_string": "IPR001344 chlorophyll AB binding",
        "ortholog_count": 12,
        "ortholog_species_count": 3,
        "paralog_count": 2,
        "interaction_count": 5,
        "introduction_report": "Intro paragraphs here.",
        "section1_markdown": "### 1. Discovery\n\nSection 1 content.",
        "section2_markdown": "### 2. Cloning\n\nSection 2 content.",
        "section3_markdown": "### 3. Functional\n\nSection 3 content.",
        "section4_markdown": "### 4. Application\n\nSection 4 content.",
    }


def _degraded_state() -> dict[str, Any]:
    return {
        "gene_found": False,
        "user_query": "Unknown gene query",
        "introduction_report": "Lit-only intro narrative.",
        "retrieve_context": "[document:1] reference text",
    }


def test_render_preamble_happy_path_writes_full_markdown() -> None:
    """gene_found=True: render assembles title + intro + bullets + sections."""
    delta = _render_preamble_node(cast(Any, _full_state()))

    content = delta["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of Os01g0177400")
    assert "Intro paragraphs here." in content
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content
    assert "**User Query ID**: Os01g0177400 (Version: rapdb)" in content
    assert "**Search ID**: Os01g0177400 (Version: msu7)" in content
    assert "**Cross-species Orthologs**: 12 across 3 species" in content
    assert "**Gene Structure**: 5 exons, 4 introns, CDS 850 bp" in content
    assert "### 1. Discovery" in content
    assert "### 4. Application" in content


def test_render_preamble_degraded_path_writes_note() -> None:
    """gene_found=False: render assembles title + intro + Note line."""
    delta = _render_preamble_node(cast(Any, _degraded_state()))

    content = delta["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of Unknown gene query")
    assert "Lit-only intro narrative." in content
    assert "*Note: A canonical gene ID could not be resolved" in content
    assert "## Gene Profiles" in content
    # Section markdowns absent on the degraded path
    assert "### 1. Discovery" not in content
    assert "### 4. Application" not in content


def test_render_preamble_bullets_logical_order() -> None:
    """Basic Information bullets follow the spec's logical grouping.

    Identity → Species/Conservation → Location/Structure → Functional
    annotation → Relationships. The user-query bullet must come
    before species, and orthologs/paralogs/interactions must come
    after functional annotation.
    """
    delta = _render_preamble_node(cast(Any, _full_state()))
    content = delta["final_response"]["choices"][0]["message"]["content"]

    idx_user_query = content.index("**User Query ID**")
    idx_species = content.index("**Species**")
    idx_location = content.index("**Genomic Location**")
    idx_go = content.index("**Core GO Annotations**")
    idx_orthologs = content.index("**Cross-species Orthologs**")

    assert idx_user_query < idx_species < idx_location < idx_go < idx_orthologs
