# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for review/report.py pure helpers and dataclasses.

Pins the supplementary-snippet formatting helpers plus the three
supplementary-context dataclasses. Async LLM/retrieval nodes
(review_node / revise_node / _feedback_rag / _audit_citations) are
covered by the live e2e suite and stay out of scope here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.review.report import (
    ReviewReportMixin,
    SupplementaryCounters,
    SupplementaryFormatState,
    SupplementaryResultContext,
    _partition_add_query_results,
)

pytestmark = pytest.mark.unit


def test_partition_add_query_results_separates_failures() -> None:
    """Protocol errors become None slots; cancellations still propagate."""
    valid = [
        {
            "chunk_id": "c1",
            "title": "t",
            "content": "body",
        }
    ]
    docs, failures = _partition_add_query_results(
        [valid, RuntimeError("hidden"), "not-docs"]
    )
    assert failures == 2
    assert docs[0] == valid
    assert docs[1] is None
    assert docs[2] is None
    with pytest.raises(KeyboardInterrupt):
        _partition_add_query_results([KeyboardInterrupt()])


def test_supplementary_counters_defaults_to_zero() -> None:
    """``SupplementaryCounters`` starts at file_id=0 and total_length=0."""
    counters = SupplementaryCounters()

    assert counters.file_id == 0
    assert counters.total_length == 0


def test_supplementary_counters_is_mutable() -> None:
    """The counters dataclass is intentionally mutable for in-loop updates."""
    counters = SupplementaryCounters()
    counters.file_id = 5
    counters.total_length = 1234

    assert counters.file_id == 5
    assert counters.total_length == 1234


def test_supplementary_format_state_is_frozen() -> None:
    """``SupplementaryFormatState`` is frozen; the counters field is not."""
    state = SupplementaryFormatState(
        query_length=500, counters=SupplementaryCounters()
    )

    with pytest.raises(FrozenInstanceError):
        setattr(state, "query_length", 999)

    state.counters.file_id += 1
    assert state.counters.file_id == 1


def test_supplementary_result_context_is_frozen() -> None:
    """``SupplementaryResultContext`` is frozen and carries query metadata."""
    ctx = SupplementaryResultContext(
        subtopic_idx=2,
        add_queries=["q1", "q2"],
        add_query_results=[[], []],
        add_doc_list=[],
        draft_content="draft",
    )

    assert ctx.subtopic_idx == 2
    with pytest.raises(FrozenInstanceError):
        setattr(ctx, "subtopic_idx", 99)


class _ReportProbe(ReviewReportMixin):
    """Public-named proxies so tests can exercise protected report helpers.

    ``_format_supplementary_results`` and ``_format_supplementary_query``
    are intentionally protected on the mixin (internal helpers). Tests
    probe through a subclass so protected access stays inside the class
    hierarchy and does not trip pylint W0212 on the test module.
    """

    def __init__(self, max_tokens: int = 4000) -> None:
        self.review_config = SimpleNamespace(MAX_TOKENS=max_tokens)

    def format_supplementary_results(
        self, context: SupplementaryResultContext
    ) -> str:
        """Public proxy for protected ``_format_supplementary_results``."""
        return self._format_supplementary_results(context)

    def format_supplementary_query(
        self,
        context: SupplementaryResultContext,
        add_result: Any,
        add_num: int,
        format_state: SupplementaryFormatState,
    ) -> str:
        """Public proxy for protected ``_format_supplementary_query``."""
        return self._format_supplementary_query(
            context, add_result, add_num, format_state
        )


def test_format_supplementary_query_returns_empty_on_exception() -> None:
    """A BaseException result yields an empty supplementary block."""
    probe = _ReportProbe()
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[RuntimeError("retrieval broke")],
        add_doc_list=[],
        draft_content="",
    )
    fmt_state = SupplementaryFormatState(
        query_length=500, counters=SupplementaryCounters()
    )

    block = probe.format_supplementary_query(
        context, RuntimeError("retrieval broke"), 0, fmt_state
    )

    assert block == ""


def test_format_supplementary_query_returns_empty_on_no_docs() -> None:
    """Empty per-query doc list yields an empty supplementary block."""
    probe = _ReportProbe()
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[[]],
        add_doc_list=[],
        draft_content="",
    )
    fmt_state = SupplementaryFormatState(
        query_length=500, counters=SupplementaryCounters()
    )

    assert probe.format_supplementary_query(context, [], 0, fmt_state) == ""


def test_format_supplementary_query_caps_at_three_docs() -> None:
    """The helper picks at most three docs per supplementary query."""
    docs = [{"title": f"D{i}", "content": "x" * 5} for i in range(6)]
    add_doc_list: list = []
    context = SupplementaryResultContext(
        subtopic_idx=1,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="",
    )
    probe = _ReportProbe()
    fmt_state = SupplementaryFormatState(
        query_length=10_000, counters=SupplementaryCounters()
    )

    block = probe.format_supplementary_query(context, docs, 0, fmt_state)

    assert "Supplementary Direction 1: q1" in block
    assert len(add_doc_list) == 3
    assert [doc["doc_id"] for doc in add_doc_list] == [
        "add document S2-001",
        "add document S2-002",
        "add document S2-003",
    ]


def test_format_supplementary_query_uses_subtopic_offset_in_doc_id() -> None:
    """Doc IDs encode subtopic_idx+1 as the "S<N>" prefix."""
    docs = [{"title": "D", "content": "x"}]
    add_doc_list: list = []
    context = SupplementaryResultContext(
        subtopic_idx=3,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="",
    )
    probe = _ReportProbe()
    fmt_state = SupplementaryFormatState(
        query_length=10_000, counters=SupplementaryCounters()
    )

    probe.format_supplementary_query(context, docs, 0, fmt_state)

    assert add_doc_list[0]["doc_id"] == "add document S4-001"


def test_format_supplementary_results_empty_when_all_queries_fail() -> None:
    """All-empty supplementary results yield an empty joined string."""
    add_doc_list: list = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1", "q2"],
        add_query_results=[[], []],
        add_doc_list=add_doc_list,
        draft_content="draft text",
    )
    probe = _ReportProbe()

    assert probe.format_supplementary_results(context) == ""
    assert not add_doc_list


def test_format_supplementary_results_joins_blocks_with_separator() -> None:
    """Non-empty per-query blocks are joined with the ``---`` separator."""
    docs_a = [{"title": "Doc A", "content": "aaa"}]
    docs_b = [{"title": "Doc B", "content": "bbb"}]
    add_doc_list: list = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["query alpha", "query beta"],
        add_query_results=[docs_a, docs_b],
        add_doc_list=add_doc_list,
        draft_content="d" * 100,
    )
    probe = _ReportProbe(max_tokens=10_000)

    block = probe.format_supplementary_results(context)

    assert "Supplementary Direction 1: query alpha" in block
    assert "Supplementary Direction 2: query beta" in block
    assert "\n\n---\n\n" in block
    assert len(add_doc_list) == 2


class _MixinSurface(ReviewReportMixin):
    """Harness that keeps the mixin public wrappers un-overridden."""

    def __init__(self, max_tokens: int = 4000) -> None:
        self.review_config = SimpleNamespace(
            MAX_TOKENS=max_tokens, PROMPT_FILE="unused.yaml"
        )
        self.chat_prompts: list[str] = []

    async def _chat(self, user_query: str) -> dict[str, Any]:
        """Record the citation-check prompt and return a rewrite."""
        self.chat_prompts.append(user_query)
        return {"choices": [{"message": {"content": "  audited-draft  "}}]}

    async def audit_citations(
        self,
        content_to_check: str,
        raw_doc_list: list[dict[str, Any]],
        add_doc_list: list[dict[str, Any]],
    ) -> str:
        """Public proxy for protected ``_audit_citations``."""
        return await self._audit_citations(
            content_to_check=content_to_check,
            raw_doc_list=raw_doc_list,
            add_doc_list=add_doc_list,
        )


def test_mixin_format_supplementary_results_public_wrapper() -> None:
    """The mixin public wrapper forwards to the protected formatter."""
    docs = [{"title": "Doc", "content": "body"}]
    add_doc_list: list[dict[str, Any]] = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="d",
    )

    block = _MixinSurface(max_tokens=10_000).format_supplementary_results(
        context
    )

    assert "Supplementary Direction 1: q1" in block
    assert len(add_doc_list) == 1


async def test_mixin_feedback_rag_public_wrapper_coerces_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public ``feedback_rag`` treats a non-list search_queries as empty."""

    async def _audit_passthrough(
        self: Any,
        *,
        content_to_check: str,
        raw_doc_list: list[dict[str, Any]],
        add_doc_list: list[dict[str, Any]],
    ) -> str:
        del self, raw_doc_list, add_doc_list
        return content_to_check

    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )
    probe = _MixinSurface()

    result = await probe.feedback_rag(
        0,
        "draft-orig",
        '{"has_critical_gaps": true, "search_queries": "not-a-list"}',
        [],
    )

    assert result == {"revised_content": "draft-orig", "add_doc_list": []}


def test_format_supplementary_query_skips_oversized_fragments() -> None:
    """A fragment longer than the per-query budget is skipped."""
    docs = [
        {"title": "Huge", "content": "x" * 400},
        {"title": "Fit", "content": "ok"},
    ]
    add_doc_list: list[dict[str, Any]] = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="",
    )
    fmt_state = SupplementaryFormatState(
        query_length=80, counters=SupplementaryCounters()
    )

    block = _ReportProbe().format_supplementary_query(
        context, docs, 0, fmt_state
    )

    assert "Fit" in block
    assert "Huge" not in block
    assert len(add_doc_list) == 1


def test_format_supplementary_query_stops_when_budget_exhausted() -> None:
    """Adding another fragment that would exceed the budget ends the loop."""
    docs = [
        {"title": "A", "content": "aaa"},
        {"title": "B", "content": "bbb"},
    ]
    add_doc_list: list[dict[str, Any]] = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="",
    )
    fmt_state = SupplementaryFormatState(
        query_length=80, counters=SupplementaryCounters()
    )

    block = _ReportProbe().format_supplementary_query(
        context, docs, 0, fmt_state
    )

    assert "Supplementary Direction 1: q1" in block
    assert len(add_doc_list) == 1
    assert add_doc_list[0]["title"] == "A"


def test_format_supplementary_query_empty_when_all_fragments_oversize() -> (
    None
):
    """Every oversized fragment leaves the supplementary block empty."""
    docs = [{"title": "Huge", "content": "x" * 400}]
    add_doc_list: list[dict[str, Any]] = []
    context = SupplementaryResultContext(
        subtopic_idx=0,
        add_queries=["q1"],
        add_query_results=[docs],
        add_doc_list=add_doc_list,
        draft_content="",
    )
    fmt_state = SupplementaryFormatState(
        query_length=20, counters=SupplementaryCounters()
    )

    block = _ReportProbe().format_supplementary_query(
        context, docs, 0, fmt_state
    )

    assert block == ""
    assert add_doc_list == []


async def test_audit_citations_returns_unchanged_without_known_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown tags and docs without ids never open a citation batch."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.review.report.get_prompt",
        lambda *_args, **_kwargs: "prompt",
    )
    probe = _MixinSurface()

    checked = await probe.audit_citations(
        content_to_check="See [document 999] and plain text.",
        raw_doc_list=[{"content": "orphan", "title": "no-id"}],
        add_doc_list=[],
    )

    assert checked == "See [document 999] and plain text."
    assert probe.chat_prompts == []


async def test_audit_citations_rewrites_and_splits_token_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long docs truncate, overflow flushes a batch, and chat rewrites."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.review.report.get_prompt",
        lambda *_args, **_kwargs: "prompt",
    )
    probe = _MixinSurface(max_tokens=120)
    long_body = "L" * 3100
    checked = await probe.audit_citations(
        content_to_check="Cite [document 001] then [document 002].",
        raw_doc_list=[
            {"doc_id": "document 001", "content": long_body},
            {"doc_id": "document 002", "content": "short"},
        ],
        add_doc_list=[],
    )

    assert checked == "audited-draft"
    assert len(probe.chat_prompts) == 2
