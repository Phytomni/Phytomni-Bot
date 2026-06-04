# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene preamble render node.

Pure string template assembly — no LLM call. Builds
``final_response.content`` from BriefGeneState fields following the
spec's preamble markdown structure (happy + degraded paths).

The Strict directive from the old ``brief_gene_function`` prompt
("display every supplied basic-genomic bullet verbatim") is
enforced structurally here by f-string template; the LLM never
touches the bullet list, so verbatim rendering is guaranteed.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _format_gene_string(state: Dict[str, Any]) -> str:
    """Pipe-join gene_name_symbol_list with gene_id_list for display."""
    symbols = state.get("gene_name_symbol_list") or []
    ids = state.get("gene_id_list") or []
    combined: List[str] = []
    for item in symbols + ids:
        if item and item not in combined:
            combined.append(item)
    return "|".join(combined) if combined else state.get("gene_id", "")


def _render_happy_preamble(state: Dict[str, Any]) -> str:
    """Render full preamble for gene_found=True path."""
    gene_id = state.get("gene_id", "")
    gene_string = _format_gene_string(state)
    return (
        f"# Brief Gene Analysis of {gene_id}\n\n"
        f"{state.get('introduction_report', '')}\n\n"
        "## Gene Profiles\n\n"
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
        f"{state.get('interaction_count', 0)} partners\n\n"
        f"{state.get('section1_markdown', '')}\n\n"
        f"{state.get('section2_markdown', '')}\n\n"
        f"{state.get('section3_markdown', '')}\n\n"
        f"{state.get('section4_markdown', '')}\n"
    )


def _render_degraded_preamble(state: Dict[str, Any]) -> str:
    """Render degraded preamble for gene_found=False path (D5.a)."""
    user_query = state.get("user_query", "")
    return (
        f"# Brief Gene Analysis of {user_query}\n\n"
        f"{state.get('introduction_report', '')}\n\n"
        "## Gene Profiles\n\n"
        "### Basic Genomic Information\n\n"
        f"- **User Query**: {user_query}\n"
        "- *Note: A canonical gene ID could not be resolved from this "
        "query against the supported species databases. The analytical "
        "summary above is derived from literature retrieval against the "
        "free-form query.*\n"
    )


def _render_preamble_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble final_response.content from state fields.

    Returns a state delta writing ``final_response`` in OpenAI
    chat-completions shape so brief_gene clients reading
    ``message.content`` get the rendered preamble markdown
    verbatim. No LLM call is made — the bullets are guaranteed
    to be verbatim by Python string formatting.
    """
    if state.get("gene_found"):
        content = _render_happy_preamble(state)
    else:
        content = _render_degraded_preamble(state)

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
