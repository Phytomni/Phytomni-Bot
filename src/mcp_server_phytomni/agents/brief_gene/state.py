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

from typing import Any, Dict, List, Required, TypedDict


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
    is_follow_up: bool


class BriefGeneOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Carries the BI annotation strings, literature retrieval docs,
    and the chat-completions-style final response plus follow-up
    questions, so parent graphs can cite gene context without
    re-running the brief_gene pipeline.
    """

    gene_id: str
    species_code: str
    go_string: str
    kegg_string: str
    interpro_string: str
    retrieved_docs: List[Dict[str, Any]]
    final_response: Dict[str, Any]
    follow_up_questions: List[str]


class BriefGeneState(TypedDict):
    """Full working state for the BriefGeneAgent LangGraph workflow.

    Carries every key both ``BriefGeneInput`` and ``BriefGeneOutput``
    expose plus internal scratch (``gene_found``, ``gene_id_list``,
    chromosomal coordinates) the graph nodes write between BI
    lookup, annotation fetch, retrieval, and the chat call.
    """

    user_query: str
    is_follow_up: bool
    gene_found: bool
    gene_id: str
    query_id_version: str
    gene_id_version: str
    species_code: str
    species_latin_name: str
    species_english_name: str
    species_all_name: str
    gene_name_symbol_list: List[str]
    gene_id_list: List[str]
    gene_chr: str
    gene_start: str
    gene_end: str
    gene_strand: str
    go_string: str
    kegg_string: str
    interpro_string: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    follow_up_questions: List[str]
    final_response: Dict[str, Any]


BriefGeneAgentState = BriefGeneState
