# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the knowledge subgraph IO TypedDicts.

Pins the public input/output contract: ``user_query`` is the only
required ``KnowledgeInput`` key (rest default inside ``arun``),
``KnowledgeOutput`` always exposes ``retrieved_docs`` +
``final_response``, and ``KnowledgeState`` carries every input +
output key plus the intermediate slots node bodies write between
them.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.knowledge.state import (
    KnowledgeAgentState,
    KnowledgeInput,
    KnowledgeOutput,
    KnowledgeState,
)

pytestmark = pytest.mark.agent


def _required(td: type) -> frozenset[str]:
    """Return ``td.__required_keys__`` via getattr.

    PEP 705 attaches ``__required_keys__`` / ``__optional_keys__`` to
    every TypedDict class, and pyright + mypy resolve the attribute
    fine. Pylint's inference layer does not model TypedDict
    introspection, so direct attribute access trips E1101 no-member.
    Routing through ``getattr`` hides the access from pylint's static
    analysis without losing runtime semantics.
    """
    return getattr(td, "__required_keys__")


def _optional(td: type) -> frozenset[str]:
    """Return ``td.__optional_keys__`` via getattr (see :func:`_required`)."""
    return getattr(td, "__optional_keys__")


def test_knowledge_input_required_keys_are_only_user_query() -> None:
    """``user_query`` is the lone required KnowledgeInput field.

    Pins the parent→subgraph contract: a parent graph mounting the
    knowledge subgraph only owes the ``user_query`` key; the
    ``obs_file_list`` / ``repo_id_dict`` / ``is_generate`` /
    ``is_follow_up`` keys default to None or the documented boolean
    inside ``arun`` so omitting them does not break the parent.
    """
    assert _required(KnowledgeInput) == frozenset({"user_query"})
    assert _optional(KnowledgeInput) == frozenset(
        {
            "locale",
            "obs_file_list",
            "repo_id_dict",
            "is_generate",
            "is_follow_up",
        }
    )


def test_knowledge_output_carries_both_answer_paths() -> None:
    """KnowledgeOutput always exposes retrieved_docs and final_response.

    Pins that ``arun`` callers can read whichever path their
    ``is_generate`` flag implied without an ``in`` guard:
    ``retrieved_docs`` for the retrieve-only branch,
    ``final_response`` for the retrieve-plus-generate branch. The
    unused branch returns the empty default rather than a missing
    key.
    """
    assert _required(KnowledgeOutput) == frozenset(
        {"retrieved_docs", "final_response"}
    )
    assert _optional(KnowledgeOutput) == frozenset()


def test_knowledge_state_covers_input_and_output_keys() -> None:
    """KnowledgeState is the union of input, intermediate, and output.

    Pins that the working state carries every ``KnowledgeInput`` key
    (so the initial state in ``arun`` can be assembled from a parent
    input dict) and every ``KnowledgeOutput`` key (so the final
    state projects back without re-keying), plus the four
    intermediate slots ``process_files`` / ``retrieve`` / ``generate``
    /  ``follow_up`` nodes write between the input and output ends.
    """
    state_keys = _required(KnowledgeState) | _optional(KnowledgeState)
    input_keys = _required(KnowledgeInput) | _optional(KnowledgeInput)
    output_keys = _required(KnowledgeOutput) | _optional(KnowledgeOutput)
    assert input_keys <= state_keys
    assert output_keys <= state_keys
    assert {
        "upload_context",
        "retrieve_context",
        "main_response",
        "follow_up_questions",
    } <= state_keys


def test_knowledge_agent_state_alias_matches_knowledge_state() -> None:
    """``KnowledgeAgentState`` is a back-compat alias for KnowledgeState.

    Pins the alias contract: legacy importers reaching for
    ``mcp_server_phytomni.agents.knowledge.KnowledgeAgentState``
    receive the same TypedDict object as ``KnowledgeState``. Without
    this property, the internal node annotations on the
    ``KnowledgeAgent`` class would diverge from the new schema and
    type checkers would silently allow drift.
    """
    assert KnowledgeAgentState is KnowledgeState
