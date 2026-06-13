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
from mcp_server_phytomni.config.defaults import BriefGeneConfig

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
    """``BriefGeneOutput`` covers BI + section outputs + chat answer.

    Parent graphs reading the subgraph result see the gene id,
    species code, the five annotation strings
    (``go_string`` / ``kegg_string`` / ``interpro_string`` /
    ``description_string`` / ``gene_structure_string``), three
    homology dicts, four section markdowns, the introduction report,
    retrieved docs, final response, and follow-up questions.
    Internal scratch (``gene_found``, coordinates, scratch lists)
    stays hidden.
    """
    hints = get_type_hints(BriefGeneOutput)
    assert set(hints.keys()) == {
        "gene_id",
        "species_code",
        "go_string",
        "kegg_string",
        "interpro_string",
        "description_string",
        "gene_structure_string",
        "orthologs_data",
        "paralogs_data",
        "interaction_data",
        "section1_markdown",
        "section2_markdown",
        "section3_markdown",
        "section4_markdown",
        "introduction_report",
        "retrieved_docs",
        "final_response",
        "follow_up_questions",
    }


def test_brief_gene_state_carries_preamble_fan_out_fields() -> None:
    """``BriefGeneState`` declares the preamble fan-out fields.

    M6 adds 16 new keys for the X3b A architecture: BI fetch
    outputs (orthologs / paralogs / interaction dicts), six count
    summaries for Basic Information bullets, gene structure
    annotation, four section LLM markdowns, the introduction
    report, and the ``gene_profile_completed_branches`` barrier
    counter (renamed from M5-era ``part1_completed_branches``).
    """
    preamble_required = {
        "orthologs_data",
        "paralogs_data",
        "interaction_data",
        "ortholog_count",
        "ortholog_species_count",
        "paralog_count",
        "interaction_count",
        "cross_species_alias_count",
        "cross_species_alias_species_count",
        "gene_structure_string",
        "section1_markdown",
        "section2_markdown",
        "section3_markdown",
        "section4_markdown",
        "introduction_report",
        "gene_profile_completed_branches",
    }
    actual = set(get_type_hints(BriefGeneState).keys())
    missing = preamble_required - actual
    assert (
        not missing
    ), f"BriefGeneState is missing preamble keys: {sorted(missing)}"


def test_brief_gene_state_carries_full_legacy_field_set() -> None:
    """``BriefGeneState`` retains every legacy ``BriefGeneAgentState`` key.

    Pins binary compatibility for internal node annotations that
    still type-hint ``BriefGeneAgentState`` (alias of
    ``BriefGeneState``). The assertion is a SUPERSET check rather
    than an exact-equality check because the chat-subgraph and
    knowledge-subgraph dual-wire branches added additive
    ``NotRequired`` scratch keys (``chat_payload`` / ``pending_post``
    / ``chat_response`` / ``retrieve_tasks`` / ``task_index`` /
    ``knowledge_input`` / ``knowledge_payload`` /
    ``pending_post_knowledge`` / ``knowledge_response`` /
    ``retrieve_indexed_results``) which are scratch-only and do not
    belong in the legacy contract. The test passes as long as every
    legacy key stays declared.
    """
    legacy_required = {
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
        "description_string",
        "retrieved_docs",
        "retrieve_context",
        "follow_up_questions",
        "final_response",
    }
    actual = set(get_type_hints(BriefGeneState).keys())
    missing = legacy_required - actual
    assert (
        not missing
    ), f"BriefGeneState is missing legacy keys: {sorted(missing)}"


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


def test_brief_gene_subgraph_exposes_conditional_sources() -> None:
    """Compiled BriefGene graph exposes the render + retrieve conditionals.

    The wired topology has FOUR conditional sources:

    * ``query_judge_node`` routes to fetch-annotation vs. direct
      retrieval based on gene_found.
    * ``chat`` routes back to the follow_up post node via
      ``make_chat_after_router``.
    * ``render_node`` routes to the follow_up prep node or END based
      on the is_follow_up flag.
    * ``retrieve_prep_tasks_node`` fans out one ``Send`` per staged
      retrieve task via ``route_retrieve_tasks``.
    """
    agent = BriefGeneAgent(brief_config=BriefGeneConfig())
    branches = set(agent.app.builder.branches.keys())
    assert branches == {
        "query_judge_node",
        "chat",
        "render_node",
        "retrieve_prep_tasks_node",
    }


def test_brief_gene_graph_has_section_fanout_and_parallel_fetch() -> None:
    """The preamble graph fans out to 4 sections after a parallel fetch.

    query_judge fans to fetch_homology (always) and conditionally to
    fetch_annotation; retrieve runs after fetch_annotation; each of the
    four section nodes gates on BOTH retrieve_reduce and fetch_homology
    before fanning into the introduction; the retired generate/chat
    answer path is gone.
    """
    section_nodes = (
        "section_discovery_node",
        "section_cloning_node",
        "section_functional_node",
        "section_application_node",
    )
    graph = BriefGeneAgent().app.get_graph()
    nodes = set(graph.nodes)
    for node in (
        "fetch_homology_interactions_node",
        *section_nodes,
        "introduction_node",
        "render_node",
    ):
        assert node in nodes, f"missing node: {node}"
    assert "generate_prep_node" not in nodes
    assert "generate_post_node" not in nodes

    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("query_judge_node", "fetch_homology_interactions_node") in edges
    assert ("fetch_annotation_node", "retrieve_prep_tasks_node") in edges
    for section in section_nodes:
        assert ("retrieve_reduce_node", section) in edges
        assert ("fetch_homology_interactions_node", section) in edges
        assert (section, "introduction_node") in edges
    assert ("introduction_node", "render_node") in edges
