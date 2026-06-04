# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene introduction LLM node.

Migrated from deep_genome ``_run_report_introduction``, adapted
for two paths: ``gene_found=True`` (input = section1-4 markdowns
concatenated, produces a 3-5 paragraph narrative intro to the
analytical body) and ``gene_found=False`` (D5.a degraded variant
— input = ``retrieve_context`` only, produces a lit-based intro).

Prompt: ``brief_gene_introduction`` (renamed from deep_genome's
``gene_function_introduction`` in the same M9 commit).
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

    On ``gene_found=True``, the ``content`` variable carries the
    section1-4 markdowns concatenated so the introduction LLM has
    the full analytical body to summarize.
    On ``gene_found=False``, ``content`` is just ``retrieve_context``
    (no sections were produced on this path).
    """
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
        content = (
            state.get("retrieve_context", "")
            or "(no literature context available)"
        )

    gene_symbols = state.get("gene_name_symbol_list") or [
        state.get("user_query", "")
    ]
    return {
        "species_string": state.get("species_english_name", ""),
        "gene_string": "|".join(gene_symbols),
        "content": content,
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
