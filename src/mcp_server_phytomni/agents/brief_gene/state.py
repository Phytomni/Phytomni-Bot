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
    Dict,
    List,
    NotRequired,
    Required,
    Tuple,
    TypedDict,
)


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
    re-running the brief_gene pipeline. ``description_string``
    carries the per-species gene description text from the
    ``annotation_gene_description`` BI table so consumer agents that
    previously fetched it inline (e.g. ``deep_genome``) can mount
    brief_gene as a subgraph and read the description through this
    output field.
    """

    gene_id: str
    species_code: str
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
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
    description_string: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    follow_up_questions: List[str]
    final_response: Dict[str, Any]
    # Additive optional keys used only when ``USE_CHAT_SUBGRAPH`` is on
    # (chat-subgraph dual-wire). ``generate_prep_node`` /
    # ``follow_up_prep_node`` stage ``chat_payload`` + ``pending_post``;
    # the shared ``chat`` mount writes the chat-completions-style
    # ``chat_response``; the matching post node reads ``chat_response``
    # to produce ``final_response`` / ``follow_up_questions``. Marked
    # ``NotRequired`` so legacy fixtures that construct
    # ``BriefGeneState`` without the chat-subgraph branch keep
    # type-checking.
    chat_payload: NotRequired[Dict[str, Any]]
    pending_post: NotRequired[str]
    chat_response: NotRequired[Dict[str, Any]]
    # Additive optional keys used only when ``USE_KNOWLEDGE_SUBGRAPH``
    # is on. ``retrieve_prep_tasks_node`` stages the per-symbol task
    # list under ``retrieve_tasks``; ``route_retrieve_tasks`` dispatches
    # each task via ``Send`` with the per-task ``knowledge_input`` and
    # ``task_index`` keys carried on the per-Send state delta; each
    # ``retrieve_worker_node`` ``ainvoke``s the shared knowledge
    # subgraph mount and writes the indexed result tuple onto the
    # ``retrieve_indexed_results`` reducer channel (concat via
    # ``operator.add``); ``retrieve_reduce_node`` sorts the tuples by
    # ``task_index``, merges the docs by score descending, applies the
    # config ``TOP_N`` cap, and projects the final ``retrieved_docs``
    # plus the ``retrieve_context`` formatted string. ``knowledge_payload``
    # / ``pending_post_knowledge`` / ``knowledge_response`` carry the
    # per-Send legs through the shared ``knowledge`` node wrapper
    # registered via ``make_knowledge_node_wrapper``.
    retrieve_tasks: NotRequired[List[Dict[str, Any]]]
    task_index: NotRequired[int]
    knowledge_input: NotRequired[Dict[str, Any]]
    knowledge_payload: NotRequired[Dict[str, Any]]
    pending_post_knowledge: NotRequired[str]
    knowledge_response: NotRequired[List[Dict[str, Any]]]
    retrieve_indexed_results: Annotated[
        List[Tuple[int, List[Dict[str, Any]]]], operator.add
    ]


BriefGeneAgentState = BriefGeneState
