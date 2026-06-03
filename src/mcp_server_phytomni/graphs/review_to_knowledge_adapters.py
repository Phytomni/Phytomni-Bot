# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from review state to knowledge subgraph IO.

Replaces ``retrieve_node``'s legacy ``asyncio.gather`` over
``ka.arun(user_query=dim, ...)`` with a Send-dispatch worker that
calls a per-instance compiled KnowledgeAgent app once per research
dimension. Supplies the ``KnowledgeInput`` projection (single-
dimension query, review-scoped ``repo_id_dict``) and the
``KnowledgeOutput`` doc-list unwrap consumed by the worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.knowledge.state import KnowledgeInput


def build_review_knowledge_input(
    dimension: str,
    repo_id_dict: Mapping[str, int],
) -> KnowledgeInput:
    """Wrap a per-dimension retrieve call into a ``KnowledgeInput`` dict.

    ReviewAgent's ``retrieve_node`` retrieves in retrieve-only mode (no
    generate / no follow-up) because the drafting step consumes raw
    retrieved doc fragments. The legacy ``ka.arun`` call passes a
    single user query (the research dimension string); the worker
    expresses the same call through the KnowledgeAgent multi-repo API
    via ``repo_id_dict``, preserving the per-repo page-size budget
    already encoded in the config's ``REPO_ID_DICT``.

    Args:
        dimension: One research dimension string used as the retrieve
            query (matches the legacy node's ``user_query`` argument).
        repo_id_dict: The review-config multi-repo token budget map
            (``REPO_ID_DICT``) driving parallel retrieval. Matches
            what the legacy ``ka.arun`` reads from the agent config.

    Returns:
        ``KnowledgeInput`` containing the required ``dimension`` string
        as ``user_query`` plus the review-scoped ``repo_id_dict``
        override and the two flag overrides that pin the retrieve-only
        path (``is_generate=False``, ``is_follow_up=False``).
    """
    return {
        "user_query": dimension,
        "repo_id_dict": dict(repo_id_dict),
        "is_generate": False,
        "is_follow_up": False,
    }


def extract_review_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project ``KnowledgeOutput.retrieved_docs`` into the doc list.

    The review drafting step reads the raw doc list and runs its own
    fragment formatting + token-budget truncation (via
    ``_dimension_fragments``); the knowledge subgraph stores the docs
    under ``KnowledgeOutput.retrieved_docs``. This helper unwraps the
    list and defaults to ``[]`` when the upstream returned no docs so
    the downstream fragment loop still iterates over a list rather
    than ``None``.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        The raw retrieved-doc list, or ``[]`` if the upstream returned
        ``None`` or omitted the key.
    """
    docs = knowledge_output.get("retrieved_docs")
    return list(docs) if docs is not None else []
