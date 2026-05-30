# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO contract + is_follow_up routing tests for BriefGeneAgent.

Pins the public ``BriefGeneInput`` / ``BriefGeneOutput`` shape and
the new ``is_follow_up`` routing branch that gates the trailing
follow-up-question hop. Reflective for the schema parts; assertion
on ``route_after_generate`` covers both default-True and explicit
False states without touching node-body LLM calls.
"""

from __future__ import annotations

from typing import cast, get_type_hints

import pytest

from mcp_server_phytomni.agents.brief_gene import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import (
    BriefGeneInput,
    BriefGeneOutput,
    BriefGeneState,
)

pytestmark = pytest.mark.agent


def _required_keys(td: type) -> set[str]:
    return set(getattr(td, "__required_keys__", set()))


def _optional_keys(td: type) -> set[str]:
    return set(getattr(td, "__optional_keys__", set()))


def test_brief_gene_input_requires_only_user_query() -> None:
    """``BriefGeneInput`` requires exactly ``user_query``.

    Parent graphs must owe the subgraph exactly one field; the
    ``is_follow_up`` toggle is optional and defaults via the state.
    """
    assert _required_keys(BriefGeneInput) == {"user_query"}
    assert "is_follow_up" in _optional_keys(BriefGeneInput)


def test_brief_gene_output_exposes_minimal_subset() -> None:
    """``BriefGeneOutput`` covers BI annotations + retrieval + chat answer.

    Parent graphs reading the subgraph result see the gene id,
    species code, the three annotation strings, retrieved docs,
    final response, and follow-up questions. Internal scratch
    (``gene_found``, coordinates, scratch lists) stays hidden.
    """
    hints = get_type_hints(BriefGeneOutput)
    assert set(hints.keys()) == {
        "gene_id",
        "species_code",
        "go_string",
        "kegg_string",
        "interpro_string",
        "retrieved_docs",
        "final_response",
        "follow_up_questions",
    }


def test_brief_gene_state_carries_full_legacy_field_set() -> None:
    """``BriefGeneState`` retains every legacy ``BriefGeneAgentState`` key.

    Pins binary compatibility for internal node annotations that
    still type-hint ``BriefGeneAgentState`` (alias of
    ``BriefGeneState``). The new ``is_follow_up`` field is the only
    additive key.
    """
    expected = {
        "user_query",
        "is_follow_up",
        "gene_found",
        "gene_id",
        "query_id_version",
        "gene_id_version",
        "species_code",
        "species_latin_name",
        "species_english_name",
        "species_all_name",
        "gene_name_symbol_list",
        "gene_id_list",
        "gene_chr",
        "gene_start",
        "gene_end",
        "gene_strand",
        "go_string",
        "kegg_string",
        "interpro_string",
        "retrieved_docs",
        "retrieve_context",
        "follow_up_questions",
        "final_response",
    }
    assert set(get_type_hints(BriefGeneState).keys()) == expected


def test_route_after_generate_defaults_to_follow_up() -> None:
    """Missing ``is_follow_up`` routes to follow-up (legacy default).

    Direct callers via ``BriefGeneAgent.arun`` see the legacy
    behavior: the trailing follow-up-question LLM hop runs unless
    a parent graph explicitly opts out.
    """
    agent = BriefGeneAgent()
    state = cast(
        BriefGeneState,
        {
            "user_query": "AT1G01010",
        },
    )
    assert agent.route_after_generate(state) == "follow_up_node"


def test_route_after_generate_skips_when_disabled() -> None:
    """Explicit ``is_follow_up=False`` short-circuits to END.

    Parent graphs mounting brief_gene as a subgraph may set the
    flag False to skip the second LLM call when they only need
    the annotation + retrieval surface.
    """
    agent = BriefGeneAgent()
    state = cast(
        BriefGeneState,
        {
            "user_query": "AT1G01010",
            "is_follow_up": False,
        },
    )
    assert agent.route_after_generate(state) == "__end__"


def test_brief_gene_subgraph_exposes_two_conditional_sources() -> None:
    """Compiled BriefGene graph routes from query_judge AND generate.

    Pins the post-IO-schema topology: ``query_judge_node`` routes
    to fetch-annotation vs. direct retrieval, ``generate_node``
    routes to follow-up vs. end. A future refactor that collapses
    one of these branches surfaces here before the manifest export.
    """
    agent = BriefGeneAgent()
    branches = set(agent.app.builder.branches.keys())
    assert branches == {"query_judge_node", "generate_node"}
