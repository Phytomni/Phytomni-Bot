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
from typing import Any

from ..agents.knowledge.state import KnowledgeInput
from ..agents.shared.options import resolve_agent_locale
from ..runtime.locale import SupportedLocale


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
    return {
        "user_query": user_query,
        "is_generate": False,
        "is_follow_up": False,
        "locale": resolve_agent_locale(locale),
    }


def extract_brief_gene_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project ``KnowledgeOutput.retrieved_docs`` into the per-worker doc list.

    Each fan-out worker reads its KnowledgeAgent subgraph's final
    state, projects out the doc list, and writes a single
    ``(task_index, doc_list)`` tuple onto the indexed-results
    reducer; the reduce node sorts the tuples back into order,
    merges the docs by score descending, and applies the
    ``TOP_N`` cap. This helper centralises the ``retrieved_docs``
    unwrap so a future KnowledgeOutput shape change lands here only.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        The raw retrieved-doc list, or ``[]`` if the upstream returned
        ``None`` or omitted the key.
    """
    docs = knowledge_output.get("retrieved_docs")
    return list(docs) if docs is not None else []
