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
    Required,
    TypedDict,
)

from ...runtime.locale import SupportedLocale
from ..shared.parallel_dispatch import ParallelDispatchState


class DeepResearchInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting review.

    ``original_user_query`` is the only Required key (matches the
    legacy state field the ``plan_node`` reads first). The
    optional ``obs_file_list`` carries uploaded OBS files when the
    review pipeline should include them as source context.
    """

    original_user_query: Required[str]
    locale: SupportedLocale
    obs_file_list: list[str]


class DeepResearchOutput(TypedDict):
    """Answer surface exposed to the parent graph after compile.

    Carries the chat-completions-style ``final_response`` plus
    ``summary_content`` (raw review text before post-processing)
    so parent graphs may quote either the chat envelope or the
    plain markdown body without re-running the review pipeline.
    """

    final_response: dict[str, Any]
    summary_content: str


class DeepResearchState(ParallelDispatchState):
    """Full working state for the DeepResearchAgent LangGraph workflow.

    Inherits ParallelDispatchState so review participates in the
    universal ``failures`` channel (operator.add concat across N
    concurrent Send workers) alongside design / network / research.
    Four fan-out sites use indexed-result accumulators, while retrieval
    has a separate private failed-index accumulator. Reduce nodes write
    the corresponding ordered outputs after every worker is classified.

    Carries every key both ``DeepResearchInput`` and
    ``DeepResearchOutput`` expose plus internal scratch (raw doc
    list, per-dimension drafts / reviews / revisions, total
    accumulated context length) the graph nodes write between
    plan, retrieve, draft, review, revise, summarize, and
    post-process.
    """

    original_user_query: str
    locale: SupportedLocale
    user_query: str
    obs_file_list: list[str]
    upload_context: str
    total_length: int
    research_dimensions: list[str]
    thesis: str
    in_scope: str
    out_of_scope: str
    search_queries: list[str]
    all_raw_doc_list: list[dict[str, Any]]
    dimension_params: list[dict[str, str]]
    draft_contents: list[str]
    review_contents: list[str]
    revised_reports: list[dict[str, str]]
    add_doc_list: Annotated[list[dict[str, Any]], operator.add]
    summary_content: str
    final_response: dict[str, Any]

    # Chat-mount fields. ordered_doc_list is forwarded so follow_up_post_node
    # does not re-run _renumber_citations on already-renumbered [document:N]
    # text (that second pass matches nothing and yields an empty list).
    chat_payload: dict[str, Any] | None
    chat_response: dict[str, Any] | None
    pending_post: str | None
    ordered_doc_list: list[dict[str, Any]] | None

    # Send-payload fields; set only during a Send invocation.
    subtopic: str | None
    dimension_total: int | None
    knowledge: str | None
    dimension: str | None
    review_draft: str | None
    original_draft: str | None
    review_feedback: str | None
    knowledge_payload: dict[str, Any] | None
    draft_content: str | None
    review_content: str | None
    raw_doc_list: list[dict[str, Any]] | None

    retrieve_indexed_results: Annotated[
        list[tuple[int, list[dict[str, Any]]]], operator.add
    ]
    retrieve_failed_indices: Annotated[list[int], operator.add]
    draft_indexed_results: Annotated[list[tuple[int, str]], operator.add]
    review_indexed_results: Annotated[list[tuple[int, str]], operator.add]
    revised_indexed_results: Annotated[list[tuple[int, str]], operator.add]

    # Write-once ordered output from the matching reduce node.
    revised_contents: list[str]

    # Human-in-the-loop approval (single-writer, no reducer).
    approval_pending: bool
    approval_decision: dict[str, Any]
    a2ui_round: int

    # Conversation adapter only; never part of the public IO schemas.
    review_operation: str | None
    report_artifact_id: str | None
    report_revision: int
