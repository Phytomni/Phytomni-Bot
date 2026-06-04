# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene introduction LLM node.

Two paths sharing the ``brief_gene_introduction`` prompt:
gene_found=True feeds section1-4 markdowns concatenated;
gene_found=False (degraded variant) feeds retrieve_context only.
"""

from __future__ import annotations

from typing import Any, Dict

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.defaults import BriefGeneConfig
from ...config.settings import get_sensitive_config
from ..chat.service import phyto_chat
from ..shared.options import build_chat_kwargs
from .state import BriefGeneAgentState

BRIEF_GENE_CONFIG = BriefGeneConfig()


def _build_introduction_context(state: BriefGeneAgentState) -> Dict[str, Any]:
    """Assemble prompt template variables.

    On ``gene_found=True``, ``content`` is the section1-4 markdowns
    concatenated (primary narrative source) and ``retrieve_results``
    is the full retrieve_context (independent citation source so
    intro can cite literature the sections did not).
    On ``gene_found=False``, ``content`` is just ``retrieve_context``
    (no sections were produced on this path); ``retrieve_results``
    falls back to the same blob so the prompt template stays well
    formed.
    """
    retrieve_context = state.get("retrieve_context", "") or ""
    if state.get("gene_found"):
        sections = "\n\n".join(
            [
                state.get("section1_markdown", "") or "",
                state.get("section2_markdown", "") or "",
                state.get("section3_markdown", "") or "",
                state.get("section4_markdown", "") or "",
            ]
        )
        content = sections.strip() or "(sections not available)"
    else:
        content = retrieve_context or "(no literature context available)"

    gene_symbols = state.get("gene_name_symbol_list") or [
        state.get("user_query", "")
    ]
    return {
        "species_string": state.get("species_english_name", ""),
        "gene_string": "|".join(gene_symbols),
        "content": content,
        "retrieve_results": retrieve_context,
        "description_string": state.get("description_string", ""),
        "go_string": state.get("go_string", ""),
        "interpro_string": state.get("interpro_string", ""),
        "kegg_string": state.get("kegg_string", ""),
        "gene_structure_string": state.get("gene_structure_string", ""),
        "homology_context": (
            f"Orthologs: {state.get('ortholog_count', 0)} across "
            f"{state.get('ortholog_species_count', 0)} species; "
            f"Paralogs: {state.get('paralog_count', 0)} in same species; "
            "Protein-protein interactions: "
            f"{state.get('interaction_count', 0)} partners"
        ),
    }


async def _run_introduction_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """Produce a 3-5 paragraph introduction summarizing sections / lit.

    Writes ``introduction_report`` state field consumed by
    ``render_node`` (brief_gene final markdown assembly) and by
    ``deep_genome``'s mount IO projection (so deep_genome's old
    ``_run_report_introduction`` LLM call is no longer needed).
    """
    sensitive_config = get_sensitive_config()
    user_query = get_prompt(
        BRIEF_GENE_CONFIG.PROMPT_FILE,
        "user/brief_gene_introduction",
        _build_introduction_context(state),
    )
    response = await phyto_chat(
        user_query=user_query,
        **build_chat_kwargs({}, BRIEF_GENE_CONFIG, sensitive_config),
    )
    return {"introduction_report": message_content(response)}
