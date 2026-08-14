# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared mappings for retrieve-only KnowledgeAgent subgraph calls.

Analyst, Review, BriefGene, and Data share only the byte-equivalent
retrieve-only projections in this module. Domain-specific query construction,
task ordering, and failure handling remain in their callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from ..agents.knowledge.retrieval_result import (
    RetrievalProtocolError,
    require_retrieval_docs,
    retrieval_unavailable_error,
)
from ..agents.knowledge.state import KnowledgeInput
from ..agents.shared.options import resolve_agent_locale
from ..runtime.locale import SupportedLocale

__all__ = [
    "build_retrieve_only_knowledge_input",
    "extract_retrieved_docs",
    "KnowledgeInputAdapter",
    "KnowledgeOutputAdapter",
    "make_knowledge_input_adapter",
    "make_knowledge_output_adapter",
]


KnowledgeInputAdapter = Callable[..., KnowledgeInput]
"""Retrieve-input adapter accepting legacy and locale-aware call shapes."""


KnowledgeOutputAdapter = Callable[
    [Mapping[str, Any]],
    list[dict[str, Any]],
]


def make_knowledge_input_adapter(
    query_key: str,
    output_key: str,
) -> KnowledgeInputAdapter:
    """Create a named retrieve-only input projection."""
    if not query_key or not output_key:
        raise ValueError("knowledge input adapter keys must be non-empty")

    def _adapter(
        query: str,
        repo_id_dict: Mapping[str, int],
        locale: SupportedLocale | None = None,
    ) -> KnowledgeInput:
        payload: dict[str, Any] = {
            query_key: query,
            output_key: dict(repo_id_dict),
            "is_generate": False,
            "is_follow_up": False,
            "locale": resolve_agent_locale(locale),
        }
        return cast(KnowledgeInput, payload)

    _adapter.__name__ = f"knowledge_input_{query_key}_{output_key}"
    _adapter.__qualname__ = _adapter.__name__
    return _adapter


def make_knowledge_output_adapter(
    response_key: str,
) -> KnowledgeOutputAdapter:
    """Create a named projection of one ``KnowledgeOutput`` list field."""
    if not response_key:
        raise ValueError("knowledge output adapter key must be non-empty")

    def _adapter(
        knowledge_output: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return _validated_output_docs(knowledge_output, response_key)

    _adapter.__name__ = f"knowledge_output_{response_key}"
    _adapter.__qualname__ = _adapter.__name__
    return _adapter


def build_retrieve_only_knowledge_input(
    user_query: str,
    *,
    locale: SupportedLocale | None = None,
    repo_id_dict: Mapping[str, int] | None = None,
) -> KnowledgeInput:
    """Build the common input shape for a retrieve-only child graph."""
    payload: KnowledgeInput = {
        "user_query": user_query,
        "is_generate": False,
        "is_follow_up": False,
        "locale": resolve_agent_locale(locale),
    }
    if repo_id_dict is not None:
        payload["repo_id_dict"] = dict(repo_id_dict)
    return payload


def extract_retrieved_docs(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the detached retrieved-doc list from a child graph result."""
    return _validated_output_docs(knowledge_output, "retrieved_docs")


def _validated_output_docs(
    knowledge_output: Mapping[str, Any],
    response_key: str,
) -> list[dict[str, Any]]:
    """Validate the complete KnowledgeOutput before projecting documents."""
    try:
        if not isinstance(knowledge_output, Mapping):
            raise RetrievalProtocolError("Invalid knowledge output")
        docs = require_retrieval_docs(
            {"doc_list": knowledge_output[response_key]}
        )
        outcome = knowledge_output["retrieval_outcome"]
        final_response = knowledge_output["final_response"]
        if not isinstance(final_response, Mapping):
            raise RetrievalProtocolError("Invalid knowledge output")
        if outcome not in ("complete", "partial", "no_match"):
            raise RetrievalProtocolError("Invalid knowledge output")
        if outcome == "no_match" and docs:
            raise RetrievalProtocolError("Invalid knowledge output")
        if outcome in ("complete", "partial") and not docs:
            raise RetrievalProtocolError("Invalid knowledge output")
        return docs
    except (KeyError, TypeError, RetrievalProtocolError) as exc:
        raise retrieval_unavailable_error() from exc
