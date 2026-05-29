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

from typing import Any, Dict, List, Required, TypedDict


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


class DeepResearchState(TypedDict):
    """Full working state for the DeepResearchAgent LangGraph workflow.

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
    add_doc_list: List[Dict[str, Any]]
    summary_content: str
    final_response: Dict[str, Any]
