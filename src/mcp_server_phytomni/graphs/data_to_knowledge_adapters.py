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
from ..runtime.locale import SupportedLocale
from .knowledge_adapters import (
    build_retrieve_only_knowledge_input,
    extract_retrieved_docs,
)


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
    return build_retrieve_only_knowledge_input(
        user_query,
        locale=locale,
        repo_id_dict={data_repo_id: page_size},
    )


def extract_data_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return retrieved docs consumed by the Data rewrite step."""
    return extract_retrieved_docs(knowledge_output)
