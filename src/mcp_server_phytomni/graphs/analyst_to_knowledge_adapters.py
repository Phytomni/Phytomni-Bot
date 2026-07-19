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
from .knowledge_adapters import (
    make_knowledge_input_adapter,
    make_knowledge_output_adapter,
)

_build_input = make_knowledge_input_adapter("user_query", "repo_id_dict")
_extract_output = make_knowledge_output_adapter("retrieved_docs")


def build_analyst_knowledge_input(
    user_query: str,
    repo_id_dict: Mapping[str, int],
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
    return _build_input(user_query, repo_id_dict)


def extract_analyst_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project ``KnowledgeOutput.retrieved_docs`` into the doc list.

    The analyst planning step reads the raw doc list; the knowledge
    subgraph stores it under ``KnowledgeOutput.retrieved_docs``. This
    helper unwraps the list and defaults to ``[]`` when the upstream
    returned no docs so the downstream prompt builder still sees a
    list rather than ``None``.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        The raw retrieved-doc list, or ``[]`` if the upstream returned
        ``None`` or omitted the key.
    """
    return _extract_output(knowledge_output)
