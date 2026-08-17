# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public IO schemas for the BriefGeneAgent subgraph.

``BriefGeneInput`` is the narrow request shape a parent graph
supplies; ``BriefGeneOutput`` exposes the BI annotation strings,
literature retrieval, and chat answer a parent graph consumes;
``BriefGeneState`` is the full working dict and stays
binary-compatible with the legacy ``BriefGeneAgentState`` alias.
"""

# NOTE: ``from __future__ import annotations`` is deliberately
# omitted. PEP 705 ``Required[]`` markers are erased by lazy
# annotations and ``TypedDict.__required_keys__`` is computed at
# class-definition time, so future annotations would silently
# drop every ``Required[]`` marker on ``BriefGeneInput``.

import operator
from typing import (
    Annotated,
    Any,
    NotRequired,
    Required,
    TypedDict,
)

from ...runtime.locale import SupportedLocale
from ..shared.parallel_dispatch import DegradedRecord


class BriefGeneInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting brief_gene.

    ``user_query`` is the only Required key; ``is_follow_up``
    toggles the trailing follow-up-question hop. The state field
    defaults to ``True`` inside ``BriefGeneAgent.arun`` so direct
    callers see the legacy follow-up behavior; parent graphs may
    set ``False`` to skip the second LLM hop when composing
    brief_gene as a subgraph.
    """

    user_query: Required[str]
    locale: SupportedLocale
    is_follow_up: bool


class BriefGeneOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Carries the five BI annotation flat strings, three homology
    dicts (orthologs / paralogs / interactions), the four section
    LLM markdowns produced by the preamble fan-out, the introduction
    report, literature retrieval docs, the chat-completions-style
    final response, and follow-up questions. ``deep_genome`` mounts this
    graph and consumes the rendered ``final_response`` verbatim as its
    report preamble (only the H1 title is swapped); the structured
    ``section{1-4}_markdown`` / ``introduction_report`` fields stay
    internal to brief_gene's own render and are not projected into the
    deep report.
    """

    gene_id: str
    species_code: str
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
    gene_structure_string: str
    orthologs_data: dict[str, Any]
    paralogs_data: dict[str, Any]
    interaction_data: dict[str, Any]
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    introduction_report: str
    retrieved_docs: list[dict[str, Any]]
    literature_degraded: list[DegradedRecord]
    final_response: dict[str, Any]
    follow_up_questions: list[str]


class BriefGeneState(TypedDict):
    """Full working state for the BriefGeneAgent LangGraph workflow.

    Carries every key both ``BriefGeneInput`` and ``BriefGeneOutput``
    expose plus internal scratch (``gene_found``, ``gene_id_list``,
    chromosomal coordinates) the graph nodes write between BI
    lookup, annotation fetch, retrieval, and the chat call.
    """

    user_query: str
    locale: SupportedLocale
    is_follow_up: bool
    gene_found: bool
    gene_id: str
    query_id_version: str
    gene_id_version: str
    species_code: str
    species_latin_name: str
    species_english_name: str
    species_all_name: str
    gene_name_symbol_list: list[str]
    gene_id_list: list[str]
    gene_chr: str
    gene_start: str
    gene_end: str
    gene_strand: str
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
    retrieved_docs: list[dict[str, Any]]
    retrieve_context: str
    follow_up_questions: list[str]
    final_response: dict[str, Any]
    # Homology + interaction BI fetch; counts feed Basic Information bullets.
    gene_structure_string: str
    orthologs_data: dict[str, Any]
    paralogs_data: dict[str, Any]
    interaction_data: dict[str, Any]
    ortholog_count: int
    ortholog_species_count: int
    paralog_count: int
    interaction_count: int
    cross_species_alias_count: int
    cross_species_alias_species_count: int
    # Four parallel section nodes write these.
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    # Rendered intro. deep_genome reuses it and skips its own intro LLM.
    introduction_report: str
    # Each section node adds 1 via operator.add; they also join
    # introduction_node on an explicit multi-source edge.
    gene_profile_completed_branches: Annotated[int, operator.add]
    # Chat-subgraph split. NotRequired so fixtures without this
    # branch still type-check.
    chat_payload: NotRequired[dict[str, Any]]
    pending_post: NotRequired[str]
    chat_response: NotRequired[dict[str, Any]]
    # Knowledge-subgraph Send fan-out. Failed and cancelled indices use
    # separate reducers; the reduce node requires every planned index
    # to land in exactly one bucket, re-raises cancellation first, then
    # merges docs by score and applies TOP_N.
    retrieve_tasks: NotRequired[list[dict[str, Any]]]
    task_index: NotRequired[int]
    task_label: NotRequired[str]
    knowledge_input: NotRequired[dict[str, Any]]
    knowledge_payload: NotRequired[dict[str, Any]]
    pending_post_knowledge: NotRequired[str]
    knowledge_response: NotRequired[list[dict[str, Any]]]
    retrieve_indexed_results: Annotated[
        list[tuple[int, list[dict[str, Any]]]], operator.add
    ]
    retrieve_failed_indices: Annotated[list[int], operator.add]
    retrieve_cancelled_indices: Annotated[list[int], operator.add]
    annotation_failed_indices: list[int]
    # Recovered per-symbol retrieve faults. Never report copy or public
    # failure metadata.
    literature_degraded: Annotated[list[DegradedRecord], operator.add]
    # Conversation adapter only; not part of the public BriefGene IO.
    conversation_operation: NotRequired[str]
    conversation_thread_id: NotRequired[str]
    active_gene_id: NotRequired[str]
    active_species_code: NotRequired[str]
    report_summary: NotRequired[str]
    evidence_refs: NotRequired[list[str]]
    report_artifact_id: NotRequired[str]
    report_revision: NotRequired[int]


BriefGeneAgentState = BriefGeneState
