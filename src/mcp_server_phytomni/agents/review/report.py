# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review and revise nodes for the deep research review workflow.

Exports ReviewReportMixin, which critiques each draft subsection, runs
supplementary retrieval for flagged gaps, revises drafts with the new
evidence, and audits citations against the available document set.
Plugs into DeepResearchAgent via multiple inheritance, sharing the
``self.review_config`` / ``self.sensitive_config`` / ``self.ka`` /
``self._chat`` surface owned by the assembling class.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...runtime.workflow_mixins import WorkflowMixinBase
from .helpers import (
    CITATION_PATTERN,
    _doc_content,
    _extract_json_object,
    _format_doc_fragment,
    _normalize_citation_id,
)

if TYPE_CHECKING:
    from .agent import DeepResearchState
else:
    DeepResearchState = Dict[str, Any]


@dataclass(frozen=True)
class SupplementaryResultContext:
    """Context used to format supplementary retrieval snippets.

    Attributes:
        subtopic_idx: Index of the revised research dimension.
        add_queries: Supplementary search queries requested by review.
        add_query_results: Retrieval results for supplementary queries.
        add_doc_list: Mutable list that receives accepted new documents.
        draft_content: Draft content used to size supplementary snippets.
    """

    subtopic_idx: int
    add_queries: List[Any]
    add_query_results: List[Any]
    add_doc_list: List[Dict[str, Any]]
    draft_content: str


@dataclass
class SupplementaryCounters:
    """Mutable counters for supplementary snippet formatting.

    Attributes:
        file_id: Next sequential supplementary document id.
        total_length: Current supplementary snippet length.
    """

    file_id: int = 0
    total_length: int = 0


@dataclass(frozen=True)
class SupplementaryFormatState:
    """Formatting limits and counters for supplementary snippets.

    Attributes:
        query_length: Per-query snippet length budget.
        counters: Mutable supplementary document counters.
    """

    query_length: int
    counters: SupplementaryCounters


class ReviewReportMixin(WorkflowMixinBase):
    """Critique, revise, and citation-audit nodes for deep research."""

    async def review_node(self: Any, state: DeepResearchState):
        """Critique each draft and request supplementary search queries.

        Args:
            state: Current workflow state with dimensions and draft text.

        Returns:
            State update containing critique payloads for each draft.
        """
        dimensions = state["research_dimensions"]
        review_tasks = []
        for di, draft_text in enumerate(state["draft_contents"]):
            review_tasks.append(
                self._chat(
                    get_prompt(
                        self.review_config.PROMPT_FILE,
                        "user/deep_research_review",
                        {
                            "current_subtopic": dimensions[di],
                            "other_subtopics": [
                                subtopic
                                for idx, subtopic in enumerate(dimensions)
                                if idx != di
                            ],
                            "draft_text": draft_text,
                        },
                    )
                )
            )
        review_results = await asyncio.gather(
            *review_tasks, return_exceptions=True
        )
        return {
            "review_contents": [
                (
                    "{}"
                    if isinstance(result, BaseException)
                    else message_content(result)
                )
                for result in review_results
            ]
        }

    async def revise_node(self: Any, state: DeepResearchState):
        """Revise each draft using critique-driven supplementary retrieval.

        Args:
            state: Current workflow state with drafts, critiques, and raw docs.

        Returns:
            State updates containing revised subsection payloads and
            supplementary document metadata.
        """
        revised_tasks = [
            self._feedback_rag(
                subtopic_idx=idx,
                draft_content=state["draft_contents"][idx],
                review_content=state["review_contents"][idx],
                raw_doc_list=state["all_raw_doc_list"],
            )
            for idx in range(len(state["research_dimensions"]))
        ]
        revised_results = await asyncio.gather(
            *revised_tasks, return_exceptions=True
        )

        revised_reports = []
        add_doc_list: List[Dict[str, Any]] = []
        for idx, result in enumerate(revised_results):
            if isinstance(result, BaseException):
                revised_reports.append(
                    {
                        "subtopic": state["research_dimensions"][idx],
                        "revised_report": state["draft_contents"][idx],
                    }
                )
                continue
            revised_reports.append(
                {
                    "subtopic": state["research_dimensions"][idx],
                    "revised_report": result.get("revised_content", ""),
                }
            )
            add_doc_list.extend(result.get("add_doc_list", []))

        return {
            "revised_reports": revised_reports,
            "add_doc_list": add_doc_list,
        }

    async def _feedback_rag(
        self: Any,
        subtopic_idx: int,
        draft_content: str,
        review_content: str,
        raw_doc_list: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Retrieve additional evidence, revise, and audit citations."""
        review_json = _extract_json_object(review_content)
        has_gaps = bool(review_json.get("has_critical_gaps", False))
        add_queries = review_json.get("search_queries", [])
        if not isinstance(add_queries, list):
            add_queries = []

        add_doc_list: List[Dict[str, Any]] = []
        content_to_check = draft_content

        if has_gaps and add_queries:
            add_query_results = await asyncio.gather(
                *[
                    self.ka.arun(
                        user_query=str(query),
                        is_generate=False,
                        is_follow_up=False,
                    )
                    for query in add_queries[:3]
                ],
                return_exceptions=True,
            )
            new_knowledge_str = self._format_supplementary_results(
                SupplementaryResultContext(
                    subtopic_idx=subtopic_idx,
                    add_queries=add_queries,
                    add_query_results=add_query_results,
                    add_doc_list=add_doc_list,
                    draft_content=draft_content,
                )
            )
            if new_knowledge_str.strip():
                feedback_response = await self._chat(
                    get_prompt(
                        self.review_config.PROMPT_FILE,
                        "user/deep_research_feedback",
                        {
                            "existing_draft": draft_content,
                            "new_snippets": new_knowledge_str,
                        },
                    )
                )
                feedback_content = message_content(feedback_response)
                if feedback_content:
                    content_to_check = feedback_content

        content_to_check = await self._audit_citations(
            content_to_check=content_to_check,
            raw_doc_list=raw_doc_list,
            add_doc_list=add_doc_list,
        )
        return {
            "revised_content": content_to_check,
            "add_doc_list": add_doc_list,
        }

    def _format_supplementary_results(
        self: Any,
        context: SupplementaryResultContext,
    ) -> str:
        """Format supplementary retrieval snippets for revision."""
        query_count = max(1, len(context.add_query_results))
        add_query_length = max(
            1,
            int(
                (self.review_config.MAX_TOKENS - len(context.draft_content))
                / query_count
            ),
        )
        format_state = SupplementaryFormatState(
            query_length=add_query_length,
            counters=SupplementaryCounters(),
        )
        add_blocks = [
            block
            for add_num, add_result in enumerate(context.add_query_results)
            if (
                block := self._format_supplementary_query(
                    context,
                    add_result,
                    add_num,
                    format_state,
                )
            )
        ]
        return "\n\n---\n\n".join(add_blocks)

    def _format_supplementary_query(
        self: Any,
        context: SupplementaryResultContext,
        add_result: Any,
        add_num: int,
        format_state: SupplementaryFormatState,
    ) -> str:
        """Format snippets for one supplementary query."""
        if isinstance(add_result, Exception) or not add_result:
            return ""

        counters = format_state.counters
        query = str(context.add_queries[add_num])
        fragments = []
        valid_doc_count = 0
        for doc in add_result:
            if valid_doc_count >= 3:
                break
            current_doc_id = (
                "add document "
                f"S{context.subtopic_idx + 1}-{counters.file_id + 1:03d}"
            )
            doc_copy = doc.copy()
            doc_copy["doc_id"] = current_doc_id
            fragment = _format_doc_fragment(doc_copy, current_doc_id)
            if len(fragment) > format_state.query_length:
                continue
            if counters.total_length + len(fragment) <= (
                format_state.query_length * (add_num + 1)
            ):
                context.add_doc_list.append(doc_copy)
                fragments.append(fragment)
                counters.total_length += len(fragment)
                counters.file_id += 1
                valid_doc_count += 1
            else:
                break
        if not fragments:
            return ""
        return (
            f"### Supplementary Direction {add_num + 1}: {query}\n\n"
            + "\n\n".join(fragments)
        )

    async def _audit_citations(
        self: Any,
        content_to_check: str,
        raw_doc_list: List[Dict[str, Any]],
        add_doc_list: List[Dict[str, Any]],
    ) -> str:
        """Ask the model to remove unsupported citations."""
        all_doc_lookup = {
            str(doc["doc_id"]): _doc_content(doc)
            for doc in [*raw_doc_list, *add_doc_list]
            if doc.get("doc_id")
        }
        current_batch_docs: Dict[str, str] = {}
        current_batch_doc_len = 0

        async def run_citation_check(batch_docs: Dict[str, str]) -> None:
            nonlocal content_to_check
            if not batch_docs:
                return
            check_response = await self._chat(
                get_prompt(
                    self.review_config.PROMPT_FILE,
                    "user/deep_research_check",
                    {
                        "input_text": content_to_check,
                        "source_docs_json": json.dumps(
                            batch_docs, ensure_ascii=False
                        ),
                    },
                )
            )
            checked_text = message_content(check_response).strip()
            if checked_text:
                content_to_check = checked_text

        for tag in set(re.findall(CITATION_PATTERN, content_to_check)):
            raw_id = _normalize_citation_id(tag.strip("[]"))
            if raw_id not in all_doc_lookup:
                continue
            doc_content = all_doc_lookup[raw_id]
            if len(doc_content) > 3000:
                doc_content = f"{doc_content[:3000]}..."
            doc_json_str = json.dumps({tag: doc_content}, ensure_ascii=False)
            added_len = len(doc_json_str)
            if (
                len(content_to_check) + current_batch_doc_len + added_len
                > self.review_config.MAX_TOKENS
            ):
                await run_citation_check(current_batch_docs)
                current_batch_docs = {}
                current_batch_doc_len = 0
            current_batch_docs[tag] = doc_content
            current_batch_doc_len += added_len

        await run_citation_check(current_batch_docs)
        return content_to_check
