# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the brief_gene preamble render node.

``_render_preamble_node`` is a pure string template assembling
``final_response.content``: title + introduction_report + ## Gene
Profiles + the shared ### Basic Genomic Information bullets +
section1-4 markdowns. The same full structure renders on the
gene-not-found path with empty annotation bullets.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.brief_gene.render import (
    _render_basic_genomic_information,
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


def _not_found_state() -> dict[str, Any]:
    return {
        "gene_found": False,
        "user_query": "Unknown gene query",
        "introduction_report": "Lit-only intro narrative.",
        "retrieve_context": "[document:1] reference text",
    }


def _degraded_state() -> dict[str, Any]:
    state = _full_state()
    state["literature_degraded"] = [
        {"task_label": "OsCAB1", "message": "boom"},
        {"task_label": "OsPHYA", "message": "boom"},
    ]
    return state


def test_render_preamble_literature_degraded_inserts_banner() -> None:
    """literature_degraded non-empty: banner appears between H1 and intro."""
    delta = _render_preamble_node(cast(Any, _degraded_state()))

    content = delta["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of Os01g0177400")
    assert "⚠️ **Literature retrieval degraded**" in content
    assert "OsCAB1, OsPHYA" in content
    # Banner sits between the H1 and the introduction so the deep_genome
    # H1-swap (first-line partition) still works and the banner rides
    # verbatim into the report.
    assert content.index("⚠️") < content.index("Intro paragraphs here.")


def test_render_preamble_happy_path_omits_banner() -> None:
    """literature_degraded empty/absent: no banner in rendered content."""
    delta = _render_preamble_node(cast(Any, _full_state()))

    content = delta["final_response"]["choices"][0]["message"]["content"]
    assert "⚠️" not in content
    assert "Literature retrieval degraded" not in content


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


def test_render_preamble_gene_not_found_renders_full_structure() -> None:
    """gene_found=False (R-GeneFound-False): full structure, empty bullets.

    The not-found path renders the SAME skeleton — title, intro,
    ## Gene Profiles, ### Basic Genomic Information — with empty
    annotation values, NOT the old minimal "could not resolve" Note.
    """
    delta = _render_preamble_node(cast(Any, _not_found_state()))

    content = delta["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of Unknown gene query")
    assert "Lit-only intro narrative." in content
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content
    assert "**User Query ID**" in content
    assert "A canonical gene ID could not be resolved" not in content


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


def test_basic_info_helper_emits_14_bullets() -> None:
    """The shared Basic Info helper renders the H3 block + every label."""
    block = _render_basic_genomic_information(cast(Any, _full_state()))

    assert block.startswith("### Basic Genomic Information")
    for label in (
        "User Query ID",
        "Search ID",
        "Gene IDs & Aliases",
        "Species",
        "Cross-species Conservation",
        "Genomic Location",
        "Gene Structure",
        "Description",
        "Core GO Annotations",
        "KEGG / MapMan Pathways",
        "InterPro Domains",
        "Cross-species Orthologs",
        "Paralogs",
        "Protein-Protein Interactions",
    ):
        assert f"**{label}**" in block
