# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from AnalystAgent state to knowledge subgraph IO.

The structural wire replaces the inline ``multi_retrieve`` call in
``method_retrieve_node`` with a ``knowledge`` node hop into a
per-instance compiled KnowledgeAgent app; this module supplies the
``KnowledgeInput`` projection and ``KnowledgeOutput`` unwrap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.knowledge.state import KnowledgeInput
from ..runtime.locale import SupportedLocale
from .knowledge_adapters import (
    make_knowledge_input_adapter,
    make_knowledge_output_adapter,
)

_build_input = make_knowledge_input_adapter("user_query", "repo_id_dict")
_extract_output = make_knowledge_output_adapter("retrieved_docs")


def build_analyst_knowledge_input(
    user_query: str,
    repo_id_dict: Mapping[str, int],
    locale: SupportedLocale | None = None,
) -> KnowledgeInput:
    """Wrap an analyst retrieve call's inputs into a ``KnowledgeInput`` dict.

    ``method_retrieve_node`` always runs in retrieve-only mode (no
    generate / no follow-up) because the analyst's planning step
    consumes the raw retrieved docs directly. ``obs_file_list`` and
    upload context are not forwarded here — the analyst chat sites
    that DO consume uploaded documents are wired separately through
    the chat subgraph, and the analyst post-knowledge node performs
    its own ``download_upload_context`` call so the legacy
    ``method_context`` shape (``upload_context`` plus
    ``retrieve_context``) is reproduced bit-equivalently.

    Args:
        user_query: The analyst goal description used as the retrieve
            query (matches the legacy node's ``state['goal_description']``
            argument to ``multi_retrieve``).
        repo_id_dict: The analyst-config multi-repo token budget map
            (``REPO_ID_DICT``) driving parallel retrieval.

    Returns:
        ``KnowledgeInput`` containing the required ``user_query`` plus
        the analyst-scoped ``repo_id_dict`` override and the two flag
        overrides that pin the retrieve-only path
        (``is_generate=False``, ``is_follow_up=False``).
    """
    return _build_input(user_query, repo_id_dict, locale)


def extract_analyst_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate ``KnowledgeOutput`` and return its detached documents.

    The analyst planning step reads the raw doc list; the knowledge
    subgraph stores it under ``KnowledgeOutput.retrieved_docs``. The
    shared strict adapter checks every required output key and the
    outcome/document relationship before returning the document list.
    A genuine ``no_match`` returns an empty list; missing or malformed
    output raises the fixed retrieval-unavailable error.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        A detached retrieved-doc list, which is empty only for a valid
        ``no_match`` outcome.
    """
    return _extract_output(knowledge_output)
