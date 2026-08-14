# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for BriefGene pure helper functions.

Pins the small synchronous helpers in agents/brief_gene/pipeline.py that
shape BI response rows, document context, and annotation strings into
the BriefGene prompt and final-payload formats. These helpers feed
every BriefGene live e2e run so a small contract drift here would
silently degrade the gene card output without surfacing on the chat
or knowledge layer tests.
"""

from __future__ import annotations

from asyncio import CancelledError
from typing import Any

import pytest

from mcp_server_phytomni.agents.brief_gene.pipeline import (
    _attach_metadata,
    _dedupe,
    _doc_content,
    _first_row,
    _format_docs,
    _go_annotation_string,
    _interpro_annotation_string,
    _mapman_annotation_string,
    _partition_annotation_results,
    _response_data,
    _split_symbols,
)

pytestmark = pytest.mark.unit


def test_response_data_returns_rows_only_for_ok_responses() -> None:
    """``_response_data`` requires both dict shape and message='ok'."""
    assert _response_data({"message": "ok", "data": [{"a": 1}]}) == [{"a": 1}]
    assert _response_data({"message": "err", "data": [{"a": 1}]}) == []
    assert _response_data({"message": "ok", "data": "not-a-list"}) == []
    assert _response_data("not-a-dict") == []


def test_first_row_returns_first_when_present_else_none() -> None:
    """``_first_row`` returns the first row when shape is valid."""
    assert _first_row({"message": "ok", "data": [{"a": 1}, {"a": 2}]}) == {
        "a": 1
    }
    assert _first_row({"message": "ok", "data": []}) is None
    assert _first_row({"message": "ok", "data": ["scalar"]}) is None


def test_split_symbols_handles_empty_and_pipe_delimited() -> None:
    """Falsy input returns []; otherwise splits on '|' and strips."""
    assert _split_symbols("") == []
    assert _split_symbols("SYM1|SYM2|SYM3") == ["SYM1", "SYM2", "SYM3"]
    assert _split_symbols("  SYM1 | SYM2 |  ") == ["SYM1", "SYM2"]


def test_dedupe_preserves_order_and_drops_falsy() -> None:
    """``_dedupe`` keeps first occurrence and skips empty strings."""
    assert _dedupe(["a", "b", "a", "", "c", "b"]) == ["a", "b", "c"]


def test_doc_content_prefers_big_content_then_content_then_empty() -> None:
    """``_doc_content`` prefers big_content, falls back to content, else ''."""
    assert _doc_content({"big_content": "BIG", "content": "small"}) == "BIG"
    assert _doc_content({"content": "small"}) == "small"
    assert _doc_content({}) == ""


def test_format_docs_truncates_when_max_tokens_exceeded() -> None:
    """``_format_docs`` stops once the next fragment would exceed max_tokens.

    Each fragment renders as ``[document N begin] {title}\\n{body}
    [document N end]`` (~41 chars even for tiny docs), so the threshold
    has to leave room for one full doc and reject the second.
    """
    doc_list: list[dict[str, Any]] = [
        {"title": "A", "content": "aaa"},
        {"title": "B", "content": "bbbbbbbb"},
        {"title": "C", "content": "ccc"},
    ]

    formatted = _format_docs(doc_list, max_tokens=50)

    assert "[document 1 begin]" in formatted
    assert "[document 2 begin]" not in formatted


def test_format_docs_threads_subtitle_into_body() -> None:
    """A subtitle is prepended on its own line above the body."""
    doc_list = [
        {
            "title": "T",
            "subtitle": "abstract",
            "content": "long body",
        }
    ]

    formatted = _format_docs(doc_list, max_tokens=200)

    assert "abstract\nlong body" in formatted


def test_attach_metadata_attaches_doc_list_and_optional_questions() -> None:
    """``_attach_metadata`` only attaches follow_up_questions when provided."""
    response: dict[str, Any] = {
        "choices": [{"message": {"content": "answer"}}]
    }

    enriched = _attach_metadata(
        response,
        doc_list=[{"file_id": "doc-a"}],
        follow_up_questions=["What next?"],
    )

    payload = enriched["choices"][0]["message"]
    assert payload["doc_list"] == [{"file_id": "doc-a"}]
    assert payload["follow_up_questions"] == ["What next?"]


def test_attach_metadata_omits_follow_up_when_none() -> None:
    """Omitted follow_up_questions leaves the field off the payload."""
    response: dict[str, Any] = {
        "choices": [{"message": {"content": "answer"}}]
    }

    enriched = _attach_metadata(response, doc_list=[{"file_id": "doc-a"}])

    assert "follow_up_questions" not in enriched["choices"][0]["message"]


def test_partition_annotation_results_keeps_empty_distinct_from_failure() -> (
    None
):
    """Valid empty BI tables are absence, not failed responses."""
    rows, failed = _partition_annotation_results(
        [
            {"message": "ok", "data": []},
            RuntimeError("BI down"),
            {"message": "ok", "data": [{"a": 1}]},
        ]
    )

    assert rows == [[], [], [{"a": 1}]]
    assert failed == [1]


def test_partition_annotation_results_propagates_cancellation() -> None:
    """Cancellation never becomes a failed BI table."""
    with pytest.raises(CancelledError):
        _partition_annotation_results([CancelledError()])


def test_go_annotation_string_prefers_core_rows_over_propagated() -> None:
    """Rows with is_propagated_from_child_term=0 are picked over =1.

    Pins the GO selection contract: when both core and propagated rows
    are present, only the core rows render into the prompt. The
    propagated rows only fall back when no core row exists.
    """
    rows = [
        {
            "go_id": "GO:0001",
            "go_name": "core",
            "is_propagated_from_child_term": "0",
        },
        {
            "go_id": "GO:0002",
            "go_name": "child",
            "is_propagated_from_child_term": "1",
        },
    ]

    assert _go_annotation_string(rows) == "GO:0001 (core)"


def test_go_annotation_string_falls_back_to_no_annotation_message() -> None:
    """An empty rows list yields the 'No annotation available.' sentinel."""
    assert _go_annotation_string([]) == "No annotation available."


def test_mapman_annotation_string_drops_uninformative_descriptions() -> None:
    """Rows containing the invalid keywords are filtered out."""
    rows = [
        {"mapman_description": "photosystem II"},
        {"mapman_description": "not assigned"},
        {"mapman_description": "unknown function"},
        {"mapman_description": "redox metabolism"},
    ]

    assert (
        _mapman_annotation_string(rows) == "photosystem II ; redox metabolism"
    )


def test_interpro_annotation_string_dedupes_repeated_names() -> None:
    """``_interpro_annotation_string`` strips empties and dedupes."""
    rows = [
        {"interpro_name": "Helix-turn-helix"},
        {"interpro_name": "  "},
        {"interpro_name": "Helix-turn-helix"},
        {"interpro_name": "Domain X"},
    ]

    assert _interpro_annotation_string(rows) == ("Helix-turn-helix ; Domain X")
