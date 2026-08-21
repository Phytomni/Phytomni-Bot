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

import json
import re
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.review import planning as review_planning
from mcp_server_phytomni.agents.review.evidence_filter import (
    extract_review_query_terms,
)
from mcp_server_phytomni.agents.review.helpers import (
    CITATION_PATTERN,
    _doc_content,
    _extract_json_object,
    _format_doc_fragment,
    _normalize_citation_id,
    _renumber_citations,
)
from mcp_server_phytomni.agents.review.planning import (
    MAX_REVIEW_DIMENSIONS,
    MIN_REVIEW_DIMENSIONS,
    RetrievalAccumulator,
    ReviewPlanningMixin,
    _bounded_research_headings,
)
from mcp_server_phytomni.agents.review.state import DeepResearchState

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
    subclass so the protected access stays inside the class hierarchy.
    """

    def __init__(self, max_tokens: int = 1000) -> None:
        self.review_config = SimpleNamespace(MAX_TOKENS=max_tokens)

    def dimension_fragments(
        self,
        dimension_result: Any,
        accumulator: RetrievalAccumulator,
        length_limit: float,
        query_terms: object = (),
    ) -> list[str]:
        """Public proxy for the protected ``_dimension_fragments`` helper."""
        return self._dimension_fragments(
            dimension_result,
            accumulator,
            length_limit,
            query_terms=query_terms,
        )


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{"message": "not-a-mapping"}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_plan_query_post_node_rejects_malformed_response_shapes(
    response: Any,
) -> None:
    """Malformed OpenAI envelopes remain invalid review dimensions."""
    probe = _PlanningProbe()

    with pytest.raises(ValueError, match="Invalid research dimensions"):
        await probe.plan_query_post_node(
            cast(DeepResearchState, {"chat_response": response})
        )


async def test_plan_query_post_node_uses_common_response_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review-specific dimension validation wraps common parser output."""
    captured: dict[str, Any] = {}

    def fake_message_content(response: Any) -> str:
        captured["response"] = response
        return "ignored"

    def fake_parse_json_object(text: str) -> dict[str, Any]:
        captured["text"] = text
        return {"Research_dimensions": ["one", "two", "three", "four"]}

    monkeypatch.setattr(
        review_planning, "message_content", fake_message_content
    )
    monkeypatch.setattr(
        review_planning,
        "parse_json_object_fragment",
        fake_parse_json_object,
    )
    response = {"choices": [{"message": {"content": "payload"}}]}
    result = await _PlanningProbe().plan_query_post_node(
        cast(DeepResearchState, {"chat_response": response})
    )

    assert captured == {"response": response, "text": "ignored"}
    assert result["research_dimensions"] == ["one", "two", "three", "four"]
    assert result["thesis"] == ""
    assert result["in_scope"] == ""
    assert result["out_of_scope"] == ""
    assert result["search_queries"] == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '{"Research_dimensions": ["A", "B", "C", "D"]}',
            ["A", "B", "C", "D"],
        ),
        (
            '```json\n{"Research_dimensions": ["W", "X", "Y", "Z"]}\n```',
            ["W", "X", "Y", "Z"],
        ),
        (
            '{"Research_dimensions": ["A", "B", "C", "D", "E"]}',
            ["A", "B", "C", "D", "E"],
        ),
    ],
)
async def test_plan_query_post_node_accepts_object_fragments(
    content: str, expected: list[str]
) -> None:
    """Valid 4-10 dimension objects keep their domain shape."""
    result = await _PlanningProbe().plan_query_post_node(
        cast(
            DeepResearchState,
            {
                "chat_response": {
                    "choices": [{"message": {"content": content}}]
                }
            },
        )
    )

    assert result["research_dimensions"] == expected
    assert result["search_queries"] == []


@pytest.mark.parametrize("content", ["not-json", '["not-an-object"]'])
async def test_plan_query_post_node_rejects_non_object_fragments(
    content: str,
) -> None:
    """Malformed and list JSON cannot satisfy review dimensions."""
    with pytest.raises(ValueError, match="Invalid research dimensions"):
        await _PlanningProbe().plan_query_post_node(
            cast(
                DeepResearchState,
                {
                    "chat_response": {
                        "choices": [{"message": {"content": content}}]
                    }
                },
            )
        )


async def test_plan_query_post_node_keeps_optional_outline_fields() -> None:
    """Additive thesis, scope, and search queries survive parsing."""
    content = (
        '{"Research_dimensions":["A","B","C","D"],'
        '"thesis":"T1","in_scope":"in","out_of_scope":"out",'
        '"search_queries":["q1","q2","q3","q4"]}'
    )
    result = await _PlanningProbe().plan_query_post_node(
        cast(
            DeepResearchState,
            {
                "chat_response": {
                    "choices": [{"message": {"content": content}}]
                }
            },
        )
    )

    assert result["research_dimensions"] == ["A", "B", "C", "D"]
    assert result["thesis"] == "T1"
    assert result["in_scope"] == "in"
    assert result["out_of_scope"] == "out"
    assert result["search_queries"] == ["q1", "q2", "q3", "q4"]


async def test_plan_query_post_node_drops_mismatched_search_queries() -> None:
    """A search-query list that does not match the heading count is empty."""
    content = (
        '{"Research_dimensions":["A","B","C","D"],'
        '"search_queries":["only-one"]}'
    )
    result = await _PlanningProbe().plan_query_post_node(
        cast(
            DeepResearchState,
            {
                "chat_response": {
                    "choices": [{"message": {"content": content}}]
                }
            },
        )
    )

    assert result["research_dimensions"] == ["A", "B", "C", "D"]
    assert result["search_queries"] == []


@pytest.mark.parametrize(
    "content",
    [
        '{"Research_dimensions": ["only", "three", "headings"]}',
        '{"Research_dimensions": []}',
    ],
)
async def test_plan_query_post_node_rejects_short_dimension_lists(
    content: str,
) -> None:
    """Fewer than four headings are unusable review dimensions."""
    with pytest.raises(ValueError, match="Invalid research dimensions"):
        await _PlanningProbe().plan_query_post_node(
            cast(
                DeepResearchState,
                {
                    "chat_response": {
                        "choices": [{"message": {"content": content}}]
                    }
                },
            )
        )


async def test_plan_query_post_node_caps_headings_at_ten() -> None:
    """An eleventh heading is dropped; the first ten stay."""
    headings = [f"H{index}" for index in range(1, 12)]
    queries = [f"q{index}" for index in range(1, 12)]
    content = json.dumps(
        {
            "Research_dimensions": headings,
            "search_queries": queries,
        }
    )
    result = await _PlanningProbe().plan_query_post_node(
        cast(
            DeepResearchState,
            {
                "chat_response": {
                    "choices": [{"message": {"content": content}}]
                }
            },
        )
    )

    assert result["research_dimensions"] == headings[:10]
    assert result["search_queries"] == []


async def test_plan_query_post_node_keeps_five_search_queries() -> None:
    """Five headings keep five matching search queries."""
    content = (
        '{"Research_dimensions":["A","B","C","D","E"],'
        '"search_queries":["q1","q2","q3","q4","q5"]}'
    )
    result = await _PlanningProbe().plan_query_post_node(
        cast(
            DeepResearchState,
            {
                "chat_response": {
                    "choices": [{"message": {"content": content}}]
                }
            },
        )
    )

    assert result["research_dimensions"] == ["A", "B", "C", "D", "E"]
    assert result["search_queries"] == ["q1", "q2", "q3", "q4", "q5"]


def test_bounded_research_headings_floor_and_cap() -> None:
    """The heading helper rejects short lists and caps long lists."""
    four = ["a", "b", "c", "d"]
    assert _bounded_research_headings(four) == four
    eleven = [f"h{index}" for index in range(11)]
    assert _bounded_research_headings(eleven) == eleven[:MAX_REVIEW_DIMENSIONS]
    with pytest.raises(ValueError, match="Invalid research dimensions"):
        _bounded_research_headings(["a", "b", "c"])
    assert MIN_REVIEW_DIMENSIONS == 4
    assert MAX_REVIEW_DIMENSIONS == 10


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


def test_dimension_fragments_skips_off_topic_documents() -> None:
    """Biomedical denylist papers never receive a document id."""
    probe = _PlanningProbe()
    accumulator = RetrievalAccumulator(raw_docs=[], current_length=0)
    docs = [
        {
            "title": "Baiting proteins with C60",
            "content": "Fullerene docking in human cells.",
        },
        {
            "title": (
                "Rice OsGL1-6 is involved in leaf cuticular wax "
                "accumulation and drought resistance"
            ),
            "content": "Antisense plants lost wax.",
        },
        {
            "title": (
                "Small molecule perturbation of the CAND1-Cullin1 "
                "cycle triggers Epstein-Barr virus reactivation"
            ),
            "content": "Viral latency.",
        },
    ]
    terms = extract_review_query_terms(
        "ZOS7-MYB60-CER1 pathway in upland rice drought"
    )

    fragments = probe.dimension_fragments(
        docs, accumulator, length_limit=10_000, query_terms=terms
    )

    assert len(fragments) == 1
    assert len(accumulator.raw_docs) == 1
    assert accumulator.raw_docs[0]["doc_id"] == "document 001"
    assert "OsGL1-6" in accumulator.raw_docs[0]["title"]
    assert "C60" not in " ".join(fragments)
    assert "Epstein-Barr" not in " ".join(fragments)
