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

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.review.report import (
    ReviewReportMixin,
    SupplementaryCounters,
    SupplementaryFormatState,
    SupplementaryResultContext,
)

pytestmark = pytest.mark.unit


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

    with pytest.raises(Exception):  # FrozenInstanceError subclasses Exception.
        state.query_length = 999  # type: ignore[misc]

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
    with pytest.raises(Exception):  # FrozenInstanceError.
        ctx.subtopic_idx = 99  # type: ignore[misc]


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
