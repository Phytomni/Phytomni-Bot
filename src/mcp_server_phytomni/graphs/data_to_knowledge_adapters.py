# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from DataAgent state to knowledge subgraph IO.

DataAgent's ``retrieve_node`` calls ``retrieve(...)`` (single-repo)
against ``DataConfig.DATA_REPO_ID`` to fetch database-scenario
context for the NL2SQL rewrite step. The structural wire replaces
that inline call with a ``knowledge`` node hop into a per-instance
compiled KnowledgeAgent app; this module supplies the
``KnowledgeInput`` projection and the ``KnowledgeOutput`` unwrap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.knowledge.state import KnowledgeInput
from ..agents.shared.options import resolve_agent_locale
from ..runtime.locale import SupportedLocale


def build_data_knowledge_input(
    user_query: str,
    data_repo_id: str,
    page_size: int,
    locale: SupportedLocale | None = None,
) -> KnowledgeInput:
    """Wrap a data retrieve call's inputs into a ``KnowledgeInput`` dict.

    DataAgent's ``retrieve_node`` runs in retrieve-only mode (no
    generate / no follow-up) because the rewrite step consumes raw
    retrieved docs through the prompt template. The legacy single-repo
    retrieve is expressed as a one-entry ``repo_id_dict`` so the
    KnowledgeAgent's multi-repo API can serve it; the dict value is
    the per-repo page size ``multi_retrieve`` threads into the
    underlying ``retrieve`` call, so passing ``DATA_PAGE_SIZE`` here
    preserves the legacy doc-count contract.

    Args:
        user_query: The user's natural-language database question
            (matches the legacy node's ``state['user_query']``
            argument to ``retrieve``).
        data_repo_id: The data-config primary knowledge repository ID
            (``DATA_REPO_ID``) the legacy retrieve targets.
        page_size: Number of documents requested from the repository
            (``DATA_PAGE_SIZE``); ``multi_retrieve`` forwards this as
            the per-repo ``page_size`` so the legacy doc count is
            preserved.

    Returns:
        ``KnowledgeInput`` containing the required ``user_query`` plus
        the data-scoped one-entry ``repo_id_dict`` override and the
        two flag overrides that pin the retrieve-only path
        (``is_generate=False``, ``is_follow_up=False``).
    """
    return {
        "user_query": user_query,
        "repo_id_dict": {data_repo_id: page_size},
        "is_generate": False,
        "is_follow_up": False,
        "locale": resolve_agent_locale(locale),
    }


def extract_data_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project ``KnowledgeOutput.retrieved_docs`` into the doc list.

    The data rewrite step reads the raw doc list and runs its own
    fragment formatting + token-budget truncation + prompt-template
    stitch; the knowledge subgraph stores the docs under
    ``KnowledgeOutput.retrieved_docs``. This helper unwraps the list
    and defaults to ``[]`` when the upstream returned no docs so the
    downstream fragment loop still iterates over a list rather than
    ``None``.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        The raw retrieved-doc list, or ``[]`` if the upstream returned
        ``None`` or omitted the key.
    """
    docs = knowledge_output.get("retrieved_docs")
    return list(docs) if docs is not None else []
