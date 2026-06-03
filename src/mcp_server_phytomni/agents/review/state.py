# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public IO schemas for the DeepResearchAgent (review) subgraph.

``DeepResearchInput`` is the narrow request shape a parent graph
supplies; ``DeepResearchOutput`` exposes the literature review
summary text and the chat-completions-style answer a parent graph
consumes; ``DeepResearchState`` is the full working dict and
stays binary-compatible with the legacy inline TypedDict.
"""

# NOTE: ``from __future__ import annotations`` is deliberately
# omitted. PEP 705 ``Required[]`` markers are erased by lazy
# annotations and ``TypedDict.__required_keys__`` is computed at
# class-definition time, so future annotations would silently
# drop every ``Required[]`` marker on ``DeepResearchInput``.

import operator
from typing import (
    Annotated,
    Any,
    Dict,
    List,
    Optional,
    Required,
    Tuple,
    TypedDict,
)

from ..shared.parallel_dispatch import ParallelDispatchState


class DeepResearchInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting review.

    ``original_user_query`` is the only Required key (matches the
    legacy state field the ``plan_node`` reads first). The
    optional ``obs_file_list`` carries uploaded OBS files when the
    review pipeline should include them as source context.
    """

    original_user_query: Required[str]
    obs_file_list: List[str]


class DeepResearchOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Carries the chat-completions-style ``final_response`` plus
    ``summary_content`` (raw review text before post-processing)
    so parent graphs may quote either the chat envelope or the
    plain markdown body without re-running the review pipeline.
    """

    final_response: Dict[str, Any]
    summary_content: str


class DeepResearchState(ParallelDispatchState):
    """Full working state for the DeepResearchAgent LangGraph workflow.

    Inherits ParallelDispatchState so review participates in the
    universal ``failures`` channel (operator.add concat across N
    concurrent Send workers) alongside design / network / research.
    Adds 16 review-specific fields organized into three groups:
    8 Send-payload transient fields, 5 indexed_results accumulators,
    and 3 final ordered output fields written by reduce_node.

    Carries every key both ``DeepResearchInput`` and
    ``DeepResearchOutput`` expose plus internal scratch (raw doc
    list, per-dimension drafts / reviews / revisions, total
    accumulated context length) the graph nodes write between
    plan, retrieve, draft, review, revise, summarize, and
    post-process.
    """

    original_user_query: str
    user_query: str
    obs_file_list: List[str]
    upload_context: str
    total_length: int
    research_dimensions: List[str]
    all_raw_doc_list: List[Dict[str, Any]]
    dimension_params: List[Dict[str, str]]
    draft_contents: List[str]
    review_contents: List[str]
    revised_reports: List[Dict[str, str]]
    add_doc_list: Annotated[List[Dict[str, Any]], operator.add]
    summary_content: str
    final_response: Dict[str, Any]

    # === Single-shot chat-mount fields (locked Step 6.0 pattern) ===
    chat_payload: Optional[Dict[str, Any]]
    chat_response: Optional[Dict[str, Any]]
    pending_post: Optional[str]

    # === Send-payload transient fields (set ONLY during Send invocation) ===
    subtopic: Optional[str]
    knowledge: Optional[str]
    dimension: Optional[str]
    review_draft: Optional[str]
    original_draft: Optional[str]
    review_feedback: Optional[str]
    add_query_input: Optional[str]
    knowledge_payload: Optional[Dict[str, Any]]
    draft_content: Optional[str]
    review_content: Optional[str]
    raw_doc_list: Optional[List[Dict[str, Any]]]

    # === Fan-out parallel accumulators (5 fields x Annotated reducer) ===
    retrieve_indexed_results: Annotated[
        List[Tuple[int, List[Dict[str, Any]]]], operator.add
    ]
    draft_indexed_results: Annotated[List[Tuple[int, str]], operator.add]
    review_indexed_results: Annotated[List[Tuple[int, str]], operator.add]
    revised_indexed_results: Annotated[List[Tuple[int, str]], operator.add]
    add_query_indexed_results: Annotated[List[Tuple[int, str]], operator.add]

    # === Fan-out final ordered output (write-once by reduce_node) ===
    # draft_contents and review_contents already declared above (kept as-is).
    # all_raw_doc_list already declared above (kept as-is).
    revised_contents: List[str]
    add_query_contents: List[str]
