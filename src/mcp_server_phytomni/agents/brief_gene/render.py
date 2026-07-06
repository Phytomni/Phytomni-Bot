# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene preamble render node — pure string template, no LLM.

Assembles ``final_response.content`` from BriefGeneState fields:
title, introduction, ``## Gene Profiles`` with a shared Basic Genomic
Information block, and the four section markdowns. The same full
structure renders on the gene-not-found path (empty annotation
bullets, literature-only sections); the section prompts self-degrade.
"""

from __future__ import annotations

from typing import Any

from ..shared.parallel_dispatch import degraded_labels
from .state import BriefGeneAgentState


def _format_gene_string(state: BriefGeneAgentState) -> str:
    """Pipe-join gene_name_symbol_list with gene_id_list for display."""
    symbols = state.get("gene_name_symbol_list") or []
    ids = state.get("gene_id_list") or []
    combined: list[str] = []
    for item in symbols + ids:
        if item and item not in combined:
            combined.append(item)
    return "|".join(combined) if combined else state.get("gene_id", "")


def _render_basic_genomic_information(state: BriefGeneAgentState) -> str:
    """Render the shared 14-bullet Basic Genomic Information block.

    Consumed verbatim by ``_render_preamble_node`` and reused as the
    introduction node's prompt context so both read one source (DRY);
    missing fields render as empty values on the gene-not-found path.
    """
    gene_id = state.get("gene_id", "")
    gene_string = _format_gene_string(state)
    return (
        "### Basic Genomic Information\n\n"
        # Identity group
        f"- **User Query ID**: {state.get('user_query', '')} "
        f"(Version: {state.get('query_id_version', '')})\n"
        f"- **Search ID**: {gene_id} "
        f"(Version: {state.get('gene_id_version', '')})\n"
        f"- **Gene IDs & Aliases**: {gene_string}\n"
        # Species + cross-species conservation group
        f"- **Species**: {state.get('species_english_name', '')} "
        f"({state.get('species_latin_name', '')}, "
        f"{state.get('species_code', '')})\n"
        f"- **Cross-species Conservation**: "
        f"{state.get('cross_species_alias_count', 0)} aliases mapped "
        f"across {state.get('cross_species_alias_species_count', 0)} "
        "species\n"
        # Location + structure group
        f"- **Genomic Location**: Chromosome {state.get('gene_chr', '')}: "
        f"{state.get('gene_start', '')} - {state.get('gene_end', '')} "
        f"({state.get('gene_strand', '')})\n"
        f"- **Gene Structure**: {state.get('gene_structure_string', '')}\n"
        f"- **Description**: {state.get('description_string', '')}\n"
        # Functional annotation group
        f"- **Core GO Annotations**: {state.get('go_string', '')}\n"
        f"- **KEGG / MapMan Pathways**: {state.get('kegg_string', '')}\n"
        f"- **InterPro Domains**: {state.get('interpro_string', '')}\n"
        # Relationships group
        f"- **Cross-species Orthologs**: "
        f"{state.get('ortholog_count', 0)} across "
        f"{state.get('ortholog_species_count', 0)} species\n"
        f"- **Paralogs**: {state.get('paralog_count', 0)} in "
        f"{state.get('species_english_name', '')}\n"
        f"- **Protein-Protein Interactions**: "
        f"{state.get('interaction_count', 0)} partners\n"
    )


def _render_degraded_banner(state: BriefGeneAgentState) -> str:
    """Return a degraded blockquote, or "" when no leg degraded.

    Empty string on the no-degradation path keeps the rendered preamble
    byte-identical to today, preserving the verbatim-sync invariant the
    deep_genome mount depends on. The banner lists the gene labels whose
    literature retrieve leg failed.
    """
    records = state.get("literature_degraded") or []
    if not records:
        return ""
    labels = ", ".join(degraded_labels(records))
    return (
        "> ⚠️ **Literature retrieval degraded** — could not fetch "
        f"literature for: {labels}.\n> Sections below may be "
        "literature-thin for those genes.\n\n"
    )


def _render_preamble_node(state: BriefGeneAgentState) -> dict[str, Any]:
    """Assemble final_response.content from state fields.

    Writes ``final_response`` in OpenAI chat-completions shape so
    brief_gene clients reading ``message.content`` get the rendered
    preamble verbatim. The same full structure renders whether or not
    the gene resolved (R-GeneFound-False): on the not-found path the
    annotation bullets are empty and the sections are literature-only.
    No LLM call — bullets are verbatim by Python string formatting.
    """
    gene_id = state.get("gene_id") or state.get("user_query", "")
    content = (
        f"# Brief Gene Analysis of {gene_id}\n\n"
        f"{_render_degraded_banner(state)}"
        f"{state.get('introduction_report', '')}\n\n"
        "## Gene Profiles\n\n"
        f"{_render_basic_genomic_information(state)}\n"
        f"{state.get('section1_markdown', '')}\n\n"
        f"{state.get('section2_markdown', '')}\n\n"
        f"{state.get('section3_markdown', '')}\n\n"
        f"{state.get('section4_markdown', '')}\n"
    )
    return {
        "final_response": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                    }
                }
            ]
        }
    }
