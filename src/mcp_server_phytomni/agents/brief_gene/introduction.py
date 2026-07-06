# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene introduction LLM node.

``_run_introduction_node`` produces ``introduction_report`` by
summarizing the shared Basic Genomic Information block plus the four
section markdowns, with the gene's annotation strings supplied as the
``brief_gene_introduction`` prompt's structured reference parameters.
Consumed by ``render_node`` and by deep_genome's verbatim preamble
consumption.
"""

from __future__ import annotations

from typing import Any

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.defaults import BriefGeneConfig
from ...config.settings import get_sensitive_config
from ..chat.service import phyto_chat
from ..shared.options import build_chat_kwargs
from .analytical_sections import _build_section_context
from .render import _render_basic_genomic_information
from .state import BriefGeneAgentState

BRIEF_GENE_CONFIG = BriefGeneConfig()


def _build_introduction_context(state: BriefGeneAgentState) -> dict[str, Any]:
    """Assemble prompt template variables.

    ``content`` carries the shared Basic Genomic Information block plus
    the four section markdowns so the introduction LLM summarizes the
    full preamble; the gene's annotation strings + homology context are
    reused from the section-context builder so the prompt's structured
    reference parameters are populated regardless of ``gene_found``.
    """
    sections = "\n\n".join(
        [
            state.get("section1_markdown", "") or "",
            state.get("section2_markdown", "") or "",
            state.get("section3_markdown", "") or "",
            state.get("section4_markdown", "") or "",
        ]
    ).strip()
    basic = _render_basic_genomic_information(state)
    context = dict(_build_section_context(state))
    context["species_string"] = context.pop("species")
    context["content"] = f"{basic}\n\n{sections}".strip()
    return context


async def _run_introduction_node(
    state: BriefGeneAgentState,
) -> dict[str, Any]:
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
