# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Four parallel section LLM nodes for the brief_gene preamble fan-out.

The four ``_run_section_<role>_node`` functions (discovery / cloning /
functional / application) each call ``phyto_chat`` with a dedicated
section prompt, write the matching ordered ``section{1-4}_markdown``
slot to state, and increment ``gene_profile_completed_branches`` by 1
so the ``_build_graph`` barrier fires once all four sections complete.
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


def _build_section_context(state: BriefGeneAgentState) -> Dict[str, Any]:
    """Assemble the prompt template variables shared by all 4 sections.

    Sections share BI annotation + literature inputs. The
    ``homology_context`` variable summarizes the orthologs / paralogs /
    interaction counts so the section LLM can reference cross-species
    patterns in §4.2 Genetic Diversity / §3.3 Networks etc. without
    needing to walk the raw ``gene_list`` dicts.
    """
    return {
        "species": state.get("species_english_name", ""),
        "gene_string": "|".join(state.get("gene_name_symbol_list") or []),
        "description_string": state.get("description_string", ""),
        "go_string": state.get("go_string", ""),
        "kegg_string": state.get("kegg_string", ""),
        "interpro_string": state.get("interpro_string", ""),
        "gene_structure_string": state.get("gene_structure_string", ""),
        "homology_context": (
            f"Orthologs: {state.get('ortholog_count', 0)} across "
            f"{state.get('ortholog_species_count', 0)} species; "
            f"Paralogs: {state.get('paralog_count', 0)} in same species; "
            "Protein-protein interactions: "
            f"{state.get('interaction_count', 0)} partners"
        ),
        "retrieve_results": state.get("retrieve_context", ""),
    }


async def _call_section_llm(
    state: BriefGeneAgentState,
    prompt_path: str,
) -> str:
    """Shared LLM invocation for a section prompt.

    Builds the rendered user_query from the section's prompt
    template + context vars, forwards through ``phyto_chat`` with
    the brief_gene config-derived kwargs, and extracts the
    string content from the chat-completions response.
    """
    sensitive_config = get_sensitive_config()
    user_query = get_prompt(
        BRIEF_GENE_CONFIG.PROMPT_FILE,
        prompt_path,
        _build_section_context(state),
    )
    response = await phyto_chat(
        user_query=user_query,
        **build_chat_kwargs({}, BRIEF_GENE_CONFIG, sensitive_config),
    )
    return message_content(response)


async def _run_section_discovery_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """§1 Discovery LLM call.

    Writes ``section1_markdown`` (the LLM-produced
    "### 1. Gene Discovery..." section) plus +1 to the
    ``gene_profile_completed_branches`` barrier counter.
    """
    markdown = await _call_section_llm(
        state, "user/brief_gene_section_discovery"
    )
    return {
        "section1_markdown": markdown,
        "gene_profile_completed_branches": 1,
    }


async def _run_section_cloning_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """§2 Cloning LLM call.

    Writes ``section2_markdown`` (the LLM-produced
    "### 2. Gene Cloning..." section) plus +1 barrier counter.
    """
    markdown = await _call_section_llm(
        state, "user/brief_gene_section_cloning"
    )
    return {
        "section2_markdown": markdown,
        "gene_profile_completed_branches": 1,
    }


async def _run_section_functional_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """§3 Functional LLM call.

    Writes ``section3_markdown`` (the LLM-produced
    "### 3. Functional Analysis..." section) plus +1 barrier counter.
    """
    markdown = await _call_section_llm(
        state, "user/brief_gene_section_functional"
    )
    return {
        "section3_markdown": markdown,
        "gene_profile_completed_branches": 1,
    }


async def _run_section_application_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """§4 Application LLM call.

    Writes ``section4_markdown`` (the LLM-produced
    "### 4. Application and Evolutionary Analysis..." section)
    plus +1 barrier counter.
    """
    markdown = await _call_section_llm(
        state, "user/brief_gene_section_application"
    )
    return {
        "section4_markdown": markdown,
        "gene_profile_completed_branches": 1,
    }
