# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared mappings for retrieve-only KnowledgeAgent subgraph calls.

Analyst and Review both pass the same ``KnowledgeInput`` fields and both
consume ``KnowledgeOutput.retrieved_docs``.  The factories in this module
centralize only those byte-equivalent projections; domain-specific query
construction, task ordering, and failure handling remain in their callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from ..agents.knowledge.state import KnowledgeInput

KnowledgeInputAdapter = Callable[
    [str, Mapping[str, int]],
    KnowledgeInput,
]
KnowledgeOutputAdapter = Callable[
    [Mapping[str, Any]],
    list[dict[str, Any]],
]


def make_knowledge_input_adapter(
    query_key: str,
    output_key: str,
) -> KnowledgeInputAdapter:
    """Create a named retrieve-only input projection.

    ``query_key`` and ``output_key`` name the two fields emitted into the
    child request.  Callers should use this factory only when both domains
    have proven that the query and repository-budget projections are
    identical.

    Args:
        query_key: Output key receiving the query string.
        output_key: Output key receiving the copied repository budget map.

    Returns:
        A synchronous adapter accepting the query and repository budget and
        returning the retrieve-only ``KnowledgeInput`` shape.

    Raises:
        ValueError: If either output key is empty.
    """
    if not query_key or not output_key:
        raise ValueError("knowledge input adapter keys must be non-empty")

    def _adapter(
        query: str,
        repo_id_dict: Mapping[str, int],
    ) -> KnowledgeInput:
        payload: dict[str, Any] = {
            query_key: query,
            output_key: dict(repo_id_dict),
            "is_generate": False,
            "is_follow_up": False,
        }
        return cast(KnowledgeInput, payload)

    _adapter.__name__ = f"knowledge_input_{query_key}_{output_key}"
    _adapter.__qualname__ = _adapter.__name__
    return _adapter


def make_knowledge_output_adapter(
    response_key: str,
) -> KnowledgeOutputAdapter:
    """Create a named projection of one ``KnowledgeOutput`` list field.

    The returned adapter copies a present list and turns a missing or
    ``None`` value into ``[]``.  This preserves the downstream retrieval
    loops' list-shaped contract without deciding how a domain handles
    failures.

    Args:
        response_key: Output key containing the retrieved document list.

    Returns:
        A synchronous adapter that unwraps the selected response list.

    Raises:
        ValueError: If ``response_key`` is empty.
    """
    if not response_key:
        raise ValueError("knowledge output adapter key must be non-empty")

    def _adapter(
        knowledge_output: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        docs = knowledge_output.get(response_key)
        return list(docs) if docs is not None else []

    _adapter.__name__ = f"knowledge_output_{response_key}"
    _adapter.__qualname__ = _adapter.__name__
    return _adapter
