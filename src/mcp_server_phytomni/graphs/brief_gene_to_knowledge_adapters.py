# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from BriefGeneAgent state to knowledge subgraph IO.

The structural wire replaces the inline ``gene_retrieve`` fan-out and
the gene_found=False ``KnowledgeAgent.arun`` call in
``retrieve_node`` with a Send-dispatched per-symbol fan-out of
``knowledge`` subgraph mounts; this module supplies the
``KnowledgeInput`` projection used by the fan-out workers and the
``KnowledgeOutput`` doc-list unwrap used by the reduce node.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ..agents.knowledge.retrieval_result import RetrievalOutcome
from ..agents.knowledge.state import KnowledgeInput
from ..runtime.locale import SupportedLocale
from .knowledge_adapters import (
    build_retrieve_only_knowledge_input,
    extract_retrieved_docs,
)


def build_brief_gene_knowledge_input(
    user_query: str,
    locale: SupportedLocale | None = None,
) -> KnowledgeInput:
    """Wrap a brief_gene retrieve call's inputs into a ``KnowledgeInput`` dict.

    Every brief_gene retrieve call runs in retrieve-only mode (no
    generate / no follow-up) — the brief_gene workflow handles its
    own ``generate_node`` and ``follow_up_node`` against the
    retrieved doc list. The single-symbol fan-out workers pass the
    legacy ``f"{species}\\n{symbol}"`` shape under ``user_query``;
    the gene_found=False fallback worker passes the raw
    ``state['user_query']`` instead. Both paths share this helper so
    a future schema extension lands once for both cases.

    Args:
        user_query: Either ``f"{species}\\n{symbol}"`` (gene_found=True
            per-symbol task) or the raw user query
            (gene_found=False fallback task).

    Returns:
        ``KnowledgeInput`` containing ``user_query`` plus the two
        flag overrides that pin the retrieve-only path
        (``is_generate=False``, ``is_follow_up=False``).
    """
    return build_retrieve_only_knowledge_input(user_query, locale=locale)


def extract_brief_gene_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], RetrievalOutcome]:
    """Return validated docs and outcome produced by one BriefGene worker."""
    docs = extract_retrieved_docs(knowledge_output)
    return docs, cast(RetrievalOutcome, knowledge_output["retrieval_outcome"])
