# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for review/helpers.py pure helpers and the planning mixin.

Pins the small synchronous helpers (_extract_json_object, _doc_content,
_format_doc_fragment, _normalize_citation_id, _renumber_citations) and
the planning mixin's _dimension_fragments bounded-budget accumulator.
These helpers feed every review run so a small contract drift here
would silently degrade citation normalization or retrieval framing
without surfacing on the integration smoke tests.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, List

import pytest

from mcp_server_phytomni.agents.review.helpers import (
    CITATION_PATTERN,
    _doc_content,
    _extract_json_object,
    _format_doc_fragment,
    _normalize_citation_id,
    _renumber_citations,
)
from mcp_server_phytomni.agents.review.planning import (
    RetrievalAccumulator,
    ReviewPlanningMixin,
)

pytestmark = pytest.mark.unit


def test_extract_json_object_returns_inner_object_when_wrapped() -> None:
    """``_extract_json_object`` finds the outermost JSON object in text."""
    payload = 'Some preamble {"a": 1, "b": [2, 3]} trailing words'

    assert _extract_json_object(payload) == {"a": 1, "b": [2, 3]}


def test_extract_json_object_returns_empty_when_braces_missing() -> None:
    """Missing braces or unparseable content yields an empty dict."""
    assert _extract_json_object("no braces here") == {}
    assert _extract_json_object("{ not valid json") == {}
    assert _extract_json_object('{"value": 1}extra}garbage') == {}


def test_extract_json_object_returns_empty_when_top_level_is_array() -> None:
    """A JSON array at the top level is rejected (helper expects an object)."""
    assert _extract_json_object("[1, 2, 3]") == {}


def test_doc_content_prefers_big_content_then_content() -> None:
    """``_doc_content`` prefers big_content over content over the empty str."""
    assert _doc_content({"big_content": "BIG", "content": "small"}) == "BIG"
    assert _doc_content({"content": "small"}) == "small"
    assert _doc_content({}) == ""


def test_format_doc_fragment_pins_begin_end_markers_and_subtitle() -> None:
    """Fragment carries [<id> begin]/[<id> end] markers and subtitle prefix."""
    doc = {"title": "Paper A", "subtitle": "abstract", "content": "body"}

    fragment = _format_doc_fragment(doc, "document 001")

    assert fragment.startswith("[document 001 begin] Paper A\n")
    assert fragment.endswith(" [document 001 end]")
    assert "abstract\nbody" in fragment


def test_normalize_citation_id_branches() -> None:
    """All four normalization branches map raw IDs to internal forms."""
    assert _normalize_citation_id("S1-001") == "add document S1-001"
    assert _normalize_citation_id("s2-042") == "add document S2-042"
    assert _normalize_citation_id("003") == "document 003"
    assert _normalize_citation_id("S004") == "document 004"
    assert _normalize_citation_id("s005") == "document 005"
    assert _normalize_citation_id("anything else") == "anything else"


def test_renumber_citations_assigns_sequential_document_ids() -> None:
    """Raw [document NNN]/[S?NNN-NNN] tags map to sequential [document:N]."""
    summary_text = (
        "First [document 001] cite, then [S1-001] supplementary, then "
        "[document 001] again."
    )
    doc_list = [
        {"doc_id": "document 001", "title": "Paper A"},
        {"doc_id": "add document S1-001", "title": "Supplement"},
    ]

    formatted, ordered = _renumber_citations(summary_text, doc_list)

    assert "[document:1]" in formatted
    assert "[document:2]" in formatted
    assert "[document 001]" not in formatted
    assert [doc["doc_id"] for doc in ordered] == [1, 2]
    assert ordered[0]["title"] == "Paper A"
    assert ordered[1]["title"] == "Supplement"


def test_renumber_citations_fills_unknown_for_missing_doc() -> None:
    """A cited tag with no matching doc entry yields a placeholder doc."""
    summary_text = "Lone [document 999] citation."

    formatted, ordered = _renumber_citations(summary_text, [])

    assert "[document:1]" in formatted
    assert ordered[0]["title"] == "Unknown Document"
    assert "missing" in ordered[0]["content"].lower()


def test_renumber_citations_sorts_adjacent_citation_blocks() -> None:
    """Adjacent [document:N] markers are deduplicated and sorted."""
    summary_text = "Multi cite [document 002][document 001][document 002] end."
    doc_list = [
        {"doc_id": "document 001", "title": "A"},
        {"doc_id": "document 002", "title": "B"},
    ]

    formatted, _ = _renumber_citations(summary_text, doc_list)

    assert "[document:1][document:2]" in formatted


def test_citation_pattern_matches_supplementary_aliases() -> None:
    """The shared CITATION_PATTERN regex covers all three citation shapes."""
    assert re.findall(CITATION_PATTERN, "[document 001]") == ["[document 001]"]
    assert re.findall(CITATION_PATTERN, "[add document S1-001]") == [
        "[add document S1-001]"
    ]
    assert re.findall(CITATION_PATTERN, "[S2-007]") == ["[S2-007]"]
    assert re.findall(CITATION_PATTERN, "[S007]") == ["[S007]"]


class _PlanningProbe(ReviewPlanningMixin):
    """Public-named proxies so tests can exercise the mixin's helpers.

    ``_dimension_fragments`` is intentionally protected on the mixin (it
    is an internal helper, not a workflow node). Tests probe through a
    subclass so the protected access stays inside the class hierarchy
    and does not trip pylint W0212 on the test module.
    """

    def __init__(self, max_tokens: int = 1000) -> None:
        self.review_config = SimpleNamespace(MAX_TOKENS=max_tokens)

    def dimension_fragments(
        self,
        dimension_result: Any,
        accumulator: RetrievalAccumulator,
        length_limit: float,
    ) -> List[str]:
        """Public proxy for the protected ``_dimension_fragments`` helper."""
        return self._dimension_fragments(
            dimension_result, accumulator, length_limit
        )


def test_dimension_fragments_returns_empty_on_exception_result() -> None:
    """A BaseException in the per-dimension result yields an empty list."""
    probe = _PlanningProbe()
    accumulator = RetrievalAccumulator(raw_docs=[], current_length=0)

    fragments = probe.dimension_fragments(
        RuntimeError("retrieval broke"), accumulator, length_limit=10_000
    )

    assert not fragments
    assert not accumulator.raw_docs
    assert accumulator.file_id == 0


def test_dimension_fragments_appends_until_length_limit_reached() -> None:
    """``_dimension_fragments`` stops once the next fragment would overflow."""
    probe = _PlanningProbe()
    accumulator = RetrievalAccumulator(raw_docs=[], current_length=0)
    docs = [
        {"title": "A", "content": "aaa"},
        {"title": "B", "content": "bbb"},
        {"title": "C", "content": "ccc"},
    ]

    fragments = probe.dimension_fragments(docs, accumulator, length_limit=80)

    assert 1 <= len(fragments) <= 2
    assert accumulator.file_id == len(fragments)
    assert len(accumulator.raw_docs) == len(fragments)
    assert all("doc_id" in doc for doc in accumulator.raw_docs)


def test_dimension_fragments_uses_three_digit_doc_id_format() -> None:
    """The mixin attaches "document NNN" zero-padded doc ids in order."""
    probe = _PlanningProbe()
    accumulator = RetrievalAccumulator(
        raw_docs=[], current_length=0, file_id=4
    )
    docs = [{"title": "A", "content": "aaa"}, {"title": "B", "content": "bbb"}]

    probe.dimension_fragments(docs, accumulator, length_limit=10_000)

    assert [doc["doc_id"] for doc in accumulator.raw_docs] == [
        "document 005",
        "document 006",
    ]
