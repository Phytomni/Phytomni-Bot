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
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...runtime.operation_instrumentation_v2 import (
    instrument_operation_invocation,
)
from ..knowledge.retrieval_result import (
    RetrievalProtocolError,
    require_retrieval_docs,
    retrieval_unavailable_error,
)
from .evidence_filter import (
    extract_review_query_terms,
    review_document_permitted,
)
from .evidence_quality import (
    compose_review_retrieval_query,
    review_evidence_identity,
    select_review_evidence,
)
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
    DeepResearchState = dict[str, Any]


# Exception only, matching ``_REVISED_WORKER_CAUGHT``. Cancellation
# and shutdown (``CancelledError`` / ``KeyboardInterrupt``) propagate
# instead and are handled in ``_partition_add_query_results``.
_ADD_QUERY_FAILURE_TYPES: tuple[type[Exception], ...] = (Exception,)


def _partition_add_query_results(
    add_query_results: list[Any],
) -> tuple[list[list[dict[str, Any]] | None], int]:
    """Normalize supplementary results without retaining failure details.

    Valid document lists, including an empty list, remain distinguishable
    from ordinary failures. Cancellation-class results are re-raised so
    shutdown never becomes a degraded report.
    """
    normalized: list[list[dict[str, Any]] | None] = []
    failure_count = 0
    for result in add_query_results:
        if isinstance(result, BaseException) and not isinstance(
            result, Exception
        ):
            raise result
        if isinstance(result, _ADD_QUERY_FAILURE_TYPES):
            normalized.append(None)
            failure_count += 1
            continue
        try:
            normalized.append(require_retrieval_docs({"doc_list": result}))
        except RetrievalProtocolError:
            normalized.append(None)
            failure_count += 1
    return normalized, failure_count


def _citation_document_lookup(
    raw_doc_list: list[dict[str, Any]],
    add_doc_list: list[dict[str, Any]],
) -> dict[str, str]:
    """Index available citation text by its stable document identifier."""
    return {
        str(doc["doc_id"]): _doc_content(doc)
        for doc in [*raw_doc_list, *add_doc_list]
        if doc.get("doc_id")
    }


def _citation_batches(
    content: str,
    document_lookup: dict[str, str],
    max_tokens: int,
) -> list[dict[str, str]]:
    """Group cited source text into prompt-sized audit batches."""
    batches: list[dict[str, str]] = []
    current_docs: dict[str, str] = {}
    current_length = 0
    for tag in set(re.findall(CITATION_PATTERN, content)):
        raw_id = _normalize_citation_id(tag.strip("[]"))
        if raw_id not in document_lookup:
            continue
        doc_content = document_lookup[raw_id]
        if len(doc_content) > 3000:
            doc_content = f"{doc_content[:3000]}..."
        added_length = len(json.dumps({tag: doc_content}, ensure_ascii=False))
        if len(content) + current_length + added_length > max_tokens:
            if current_docs:
                batches.append(current_docs)
            current_docs = {}
            current_length = 0
        current_docs[tag] = doc_content
        current_length += added_length
    if current_docs:
        batches.append(current_docs)
    return batches


@dataclass(frozen=True, slots=True)
class _SupplementaryEvidenceScope:
    """Review scope used to rank and filter supplementary evidence."""

    original_query: str = ""
    subtopic: str = ""
    seen_identities: set[str] | None = None
    query_terms: tuple[str, ...] = ()


class _SupplementaryScopeOptions(TypedDict, total=False):
    """Optional scope accepted by ``SupplementaryResultContext``."""

    original_query: str
    subtopic: str
    seen_identities: set[str] | None
    query_terms: tuple[str, ...]


_SUPPLEMENTARY_SCOPE_FIELDS = (
    "original_query",
    "subtopic",
    "seen_identities",
    "query_terms",
)


def _supplementary_evidence_scope(
    values: tuple[Any, ...],
    options: _SupplementaryScopeOptions,
) -> _SupplementaryEvidenceScope:
    """Resolve legacy positional and typed keyword scope values."""
    if len(values) > len(_SUPPLEMENTARY_SCOPE_FIELDS):
        raise TypeError("too many positional scope values")
    resolved: dict[str, Any] = dict(options)
    unknown = set(resolved).difference(_SUPPLEMENTARY_SCOPE_FIELDS)
    if unknown:
        raise TypeError(f"unexpected keyword argument '{sorted(unknown)[0]}'")
    for name, value in zip(_SUPPLEMENTARY_SCOPE_FIELDS, values, strict=False):
        if name in resolved:
            raise TypeError(f"multiple values for argument '{name}'")
        resolved[name] = value
    return _SupplementaryEvidenceScope(
        original_query=resolved.get("original_query", ""),
        subtopic=resolved.get("subtopic", ""),
        seen_identities=resolved.get("seen_identities"),
        query_terms=resolved.get("query_terms", ()),
    )


@dataclass(frozen=True, init=False)
class SupplementaryResultContext:
    """Context used to format supplementary retrieval snippets.

    Attributes:
        subtopic_idx: Index of the revised research dimension.
        add_queries: Supplementary search queries requested by review.
        add_query_results: Retrieval results for supplementary queries.
        add_doc_list: Mutable list that receives accepted new documents.
        draft_content: Draft content used to size supplementary snippets.
        query_terms: Optional gene or crop tokens from the parent review.
    """

    subtopic_idx: int
    add_queries: list[Any]
    add_query_results: list[Any]
    add_doc_list: list[dict[str, Any]]
    draft_content: str
    _scope: _SupplementaryEvidenceScope = _SupplementaryEvidenceScope()

    def __init__(
        self,
        subtopic_idx: int,
        add_queries: list[Any],
        add_query_results: list[Any],
        add_doc_list: list[dict[str, Any]],
        draft_content: str,
        *scope_values: Any,
        **scope_options: Unpack[_SupplementaryScopeOptions],
    ) -> None:
        """Build formatting context with a compact evidence-scope value."""
        object.__setattr__(self, "subtopic_idx", subtopic_idx)
        object.__setattr__(self, "add_queries", add_queries)
        object.__setattr__(self, "add_query_results", add_query_results)
        object.__setattr__(self, "add_doc_list", add_doc_list)
        object.__setattr__(self, "draft_content", draft_content)
        object.__setattr__(
            self,
            "_scope",
            _supplementary_evidence_scope(scope_values, scope_options),
        )

    @property
    def original_query(self) -> str:
        """Return the parent research question for relevance ranking."""
        return self._scope.original_query

    @property
    def subtopic(self) -> str:
        """Return the research dimension being revised."""
        return self._scope.subtopic

    @property
    def seen_identities(self) -> set[str] | None:
        """Return evidence identities already present in the draft."""
        return self._scope.seen_identities

    @property
    def query_terms(self) -> tuple[str, ...]:
        """Return domain terms that supplementary evidence must respect."""
        return self._scope.query_terms


class _FeedbackScopeOptions(TypedDict, total=False):
    """Optional research scope for the compatible feedback entrypoints."""

    original_query: str
    subtopic: str


@dataclass(frozen=True, slots=True)
class _FeedbackRequest:
    """Complete immutable input to one evidence-feedback pass."""

    subtopic_idx: int
    draft_content: str
    review_content: str
    raw_doc_list: list[dict[str, Any]]
    original_query: str = ""
    subtopic: str = ""


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


class ReviewReportMixin:
    """Critique, revise, and citation-audit nodes for deep research.
    The report helpers share the review agent's configured retrieval context.
    """

    async def feedback_rag(
        self: Any,
        subtopic_idx: int,
        draft_content: str,
        review_content: str,
        raw_doc_list: list[dict[str, Any]],
        **scope_options: Unpack[_FeedbackScopeOptions],
    ) -> dict[str, Any]:
        """Run evidence feedback through the Review report seam."""
        return await self._feedback_rag(
            subtopic_idx,
            draft_content,
            review_content,
            raw_doc_list,
            **scope_options,
        )

    def format_supplementary_results(
        self: Any,
        context: SupplementaryResultContext,
    ) -> str:
        """Format supplementary evidence through the report seam."""
        return self._format_supplementary_results(context)

    async def _feedback_rag(
        self: Any,
        subtopic_idx: int,
        draft_content: str,
        review_content: str,
        raw_doc_list: list[dict[str, Any]],
        **scope_options: Unpack[_FeedbackScopeOptions],
    ) -> dict[str, Any]:
        """Retrieve additional evidence, revise, and audit citations.

        Per-call ``self.ka.arun`` failures remain internal evidence
        decisions. Existing main or supplementary evidence permits silent
        continuation; when no reliable evidence remains the fixed retrieval
        error is raised. Cancellation-class results propagate.
        """
        unknown = set(scope_options).difference({"original_query", "subtopic"})
        if unknown:
            raise TypeError(
                f"unexpected keyword argument '{sorted(unknown)[0]}'"
            )
        return await self._feedback_from_request(
            _FeedbackRequest(
                subtopic_idx=subtopic_idx,
                draft_content=draft_content,
                review_content=review_content,
                raw_doc_list=raw_doc_list,
                original_query=scope_options.get("original_query", ""),
                subtopic=scope_options.get("subtopic", ""),
            )
        )

    async def _feedback_from_request(
        self: Any,
        request: _FeedbackRequest,
    ) -> dict[str, Any]:
        """Apply review gaps, supplementary retrieval, and citation audit."""
        review_json = _extract_json_object(request.review_content)
        add_queries = review_json.get("search_queries", [])
        if not isinstance(add_queries, list):
            add_queries = []

        add_doc_list: list[dict[str, Any]] = []
        content_to_check = request.draft_content

        if (
            review_json.get("has_critical_gaps", False)
            and not review_json.get("off_topic", False)
            and add_queries
        ):
            content_to_check = await self._supplement_draft(
                request,
                add_queries,
                add_doc_list,
            )

        content_to_check = await self._audit_citations(
            content_to_check=content_to_check,
            raw_doc_list=request.raw_doc_list,
            add_doc_list=add_doc_list,
        )
        return {
            "revised_content": content_to_check,
            "add_doc_list": add_doc_list,
        }

    async def _supplement_draft(
        self: Any,
        request: _FeedbackRequest,
        add_queries: list[Any],
        add_doc_list: list[dict[str, Any]],
    ) -> str:
        """Retrieve review-scoped evidence and revise the draft when useful."""
        scoped_queries = [
            compose_review_retrieval_query(
                original_query=request.original_query,
                dimension=request.subtopic,
                supplementary_query=str(query),
            )
            for query in add_queries[:3]
        ]
        query_results = await asyncio.gather(
            *[
                self.ka.arun(
                    user_query=query,
                    is_generate=False,
                    is_follow_up=False,
                )
                for query in scoped_queries
            ],
            return_exceptions=True,
        )
        normalized_results, failure_count = _partition_add_query_results(
            query_results
        )
        new_knowledge = self._format_supplementary_results(
            SupplementaryResultContext(
                subtopic_idx=request.subtopic_idx,
                add_queries=add_queries,
                add_query_results=normalized_results,
                add_doc_list=add_doc_list,
                draft_content=request.draft_content,
                original_query=request.original_query,
                subtopic=request.subtopic,
                seen_identities={
                    identity
                    for document in request.raw_doc_list
                    if (identity := review_evidence_identity(document))
                },
            )
        )
        if failure_count and not request.raw_doc_list and not add_doc_list:
            raise retrieval_unavailable_error()
        if not new_knowledge.strip():
            return request.draft_content
        feedback_response = await self._chat(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_feedback",
                {
                    "existing_draft": request.draft_content,
                    "new_snippets": new_knowledge,
                },
            )
        )
        return message_content(feedback_response) or request.draft_content

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
        if isinstance(add_result, BaseException) or not add_result:
            return ""

        counters = format_state.counters
        query = str(context.add_queries[add_num])
        fragments = []
        valid_doc_count = 0
        query_terms = {
            *context.query_terms,
            *extract_review_query_terms(query),
        }
        selected_documents = select_review_evidence(
            original_query=context.original_query,
            dimension=(
                f"{context.subtopic} {query}".strip()
                if context.subtopic
                else query
            ),
            documents=add_result,
            seen_identities=context.seen_identities,
        )
        for doc in selected_documents:
            if valid_doc_count >= 3:
                break
            if not isinstance(doc, dict):
                continue
            if not review_document_permitted(doc, query_terms):
                continue
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
        raw_doc_list: list[dict[str, Any]],
        add_doc_list: list[dict[str, Any]],
    ) -> str:
        """Ask the model to remove unsupported citations."""
        batches = _citation_batches(
            content_to_check,
            _citation_document_lookup(raw_doc_list, add_doc_list),
            self.review_config.MAX_TOKENS,
        )
        for index, batch_docs in enumerate(batches):
            content_to_check = await self._check_citation_batch(
                content_to_check,
                batch_docs,
                ordinal=index + 1,
                total=len(batches),
            )
        return content_to_check

    async def _check_citation_batch(
        self: Any,
        content: str,
        batch_docs: dict[str, str],
        *,
        ordinal: int,
        total: int,
    ) -> str:
        """Apply one instrumented citation audit batch to report content."""

        async def check_batch() -> Any:
            return await self._chat(
                get_prompt(
                    self.review_config.PROMPT_FILE,
                    "user/deep_research_check",
                    {
                        "input_text": content,
                        "source_docs_json": json.dumps(
                            batch_docs, ensure_ascii=False
                        ),
                    },
                )
            )

        check_response = await instrument_operation_invocation(
            "review.citation_check",
            check_batch,
            detail={"ordinal": ordinal, "total": total},
        )
        return message_content(check_response).strip() or content
