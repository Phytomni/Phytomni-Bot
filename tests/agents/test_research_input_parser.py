# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for strict, side-effect-free Research pasted-input parsing."""

from __future__ import annotations

import hashlib

import pytest

from mcp_server_phytomni.agents.research import input_contracts
from mcp_server_phytomni.agents.research.input_contracts import (
    ResearchInputFailure,
)
from mcp_server_phytomni.agents.research.input_parser import (
    has_explicit_research_data_syntax,
    parse_research_input,
)

pytestmark = pytest.mark.agent


def _pin_object_ref_allowlist(
    monkeypatch: pytest.MonkeyPatch, *keys: str
) -> None:
    """Pin the object-ref allowlist for one parser test."""
    monkeypatch.setattr(
        input_contracts,
        "RESEARCH_OBJECT_REF_ALLOWLIST",
        frozenset(keys),
    )


def test_candidate_span_round_trips_unicode_and_crlf() -> None:
    """Candidate offsets are Python code-point indexes into untouched input."""
    query = '分析甲\r\ndata: {"obs://dev-bucket/a.tsv": "乙"}'

    parsed = parse_research_input(query, "dev-bucket")

    candidate = parsed.candidates[0]
    assert query[slice(candidate.source_start, candidate.source_end)] == (
        candidate.exact_reference
    )
    assert parsed.effective_query == "分析甲\r\n"
    assert parsed.effective_to_original == tuple(range(len("分析甲\r\n")))
    assert (
        parsed.original_query_digest
        == hashlib.sha256(query.encode("utf-8")).hexdigest()
    )
    assert parsed.original_query_length == len(query)


def test_trailing_json_preserves_empty_hint_as_none_and_source_order() -> None:
    """A strict trailing object preserves exact keys and JSON pair ordering."""
    query = (
        '请比较\nDATA: {"obs://DEV-BUCKET/a.TSV": "", '
        '"obs://dev-bucket/b.vcf": "calls"}'
    )

    parsed = parse_research_input(query, "dev-bucket")

    assert parsed.effective_query == "请比较\n"
    assert parsed.removed_spans[0].grammar == "trailing_json"
    assert [candidate.exact_reference for candidate in parsed.candidates] == [
        "obs://DEV-BUCKET/a.TSV",
        "obs://dev-bucket/b.vcf",
    ]
    assert [candidate.user_hint for candidate in parsed.candidates] == [
        None,
        "calls",
    ]
    assert [candidate.ordinal for candidate in parsed.candidates] == [0, 1]
    assert parsed.candidates[0].comparison_key == "obs://dev-bucket/a.TSV"


def test_fenced_json_removes_only_associated_data_span() -> None:
    """A fenced strict object can sit between prose without consuming prose."""
    query = (
        "before\n"
        "data: \n"
        "```json\n"
        '{"obs://dev-bucket/a.fastq.gz": "reads"}\n'
        "```\n"
        "after"
    )

    parsed = parse_research_input(query, "dev-bucket")

    assert parsed.effective_query == "before\n\nafter"
    assert parsed.removed_spans[0].grammar == "fenced_json"
    assert query[
        slice(parsed.removed_spans[0].start, parsed.removed_spans[0].end)
    ] == (
        "data: \n"
        "```json\n"
        '{"obs://dev-bucket/a.fastq.gz": "reads"}\n'
        "```"
    )
    candidate = parsed.candidates[0]
    assert query[slice(candidate.source_start, candidate.source_end)] == (
        "obs://dev-bucket/a.fastq.gz"
    )
    assert candidate.user_hint == "reads"


def test_standalone_lines_accept_lf_crlf_and_one_ascii_tab() -> None:
    """Only complete logical reference lines become candidates."""
    query = (
        "目标\n"
        "obs://dev-bucket/reads.fastq.gz\tRNA-seq\r\n"
        "obs://dev-bucket/counts.tsv\t\n"
        "结尾"
    )

    parsed = parse_research_input(query, "dev-bucket")

    assert parsed.effective_query == "目标\n\r\n\n结尾"
    assert [candidate.user_hint for candidate in parsed.candidates] == [
        "RNA-seq",
        None,
    ]
    assert [span.grammar for span in parsed.removed_spans] == [
        "standalone_tab",
        "standalone_tab",
    ]
    assert [candidate.ordinal for candidate in parsed.candidates] == [0, 1]


@pytest.mark.parametrize(
    ("query", "code"),
    [
        (
            'data: {"obs://dev-bucket/a.tsv": "one", '
            '"obs://dev-bucket/a.tsv": "two"}',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": "hint",}',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": "hint" /* note */}',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": null}',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": 1}',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": "hint"} {}',
            "research_data_block_invalid",
        ),
        (
            "data: []",
            "research_data_block_invalid",
        ),
        (
            "data: 42",
            "research_data_block_invalid",
        ),
        (
            'data: "not an object"',
            "research_data_block_invalid",
        ),
        (
            'data: {"obs://other-bucket/a.tsv": "hint"}',
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/a.tsv, hint",
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/a.tsv\thint\textra",
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/../a.tsv",
            "research_dataset_path_invalid",
        ),
        (
            "C:\\\\data\\a.tsv",
            "research_dataset_path_invalid",
        ),
        (
            "\\\\server\\share\\a.tsv",
            "research_dataset_path_invalid",
        ),
        (
            "/tmp/a.tsv",
            "research_dataset_path_invalid",
        ),
        (
            "https://example.test/a.tsv",
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/a\x00.tsv",
            "research_dataset_path_invalid",
        ),
        (
            'data: {"obs://dev-bucket/a.tsv": "bad\\u0000hint"}',
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/a.tsv\tbad\x1fhint",
            "research_dataset_path_invalid",
        ),
        (
            "obs://dev-bucket/a.unknown",
            "research_dataset_format_unsupported",
        ),
    ],
)
def test_rejects_ambiguous_or_unsafe_explicit_references(
    query: str,
    code: str,
) -> None:
    """Explicit datasets fail closed with one stable domain code."""
    with pytest.raises(ResearchInputFailure) as caught:
        parse_research_input(query, "dev-bucket")

    assert caught.value.code == code
    assert caught.value.stage == "input_resolution"
    assert caught.value.retryable is False


def test_rejects_duplicate_references_across_approved_grammars() -> None:
    """Comparison identities reject duplicate references across grammars."""
    query = (
        "data:\n"
        "```json\n"
        '{"obs://dev-bucket/a.tsv": "first"}\n'
        "```\n"
        "obs://dev-bucket/a.tsv\tsecond"
    )

    with pytest.raises(ResearchInputFailure) as caught:
        parse_research_input(query, "dev-bucket")

    assert caught.value.code == "research_dataset_duplicate"


@pytest.mark.parametrize("escaped_control", ("0009", "000a", "000d"))
def test_rejects_escaped_control_characters_in_json_hints(
    escaped_control: str,
) -> None:
    """Decoded JSON hints never inherit grammar-delimiter exceptions."""
    query = (
        f'data: {{"obs://dev-bucket/a.tsv": "bad\\u{escaped_control}hint"}}'
    )

    with pytest.raises(ResearchInputFailure) as caught:
        parse_research_input(query, "dev-bucket")

    assert caught.value.code == "research_dataset_path_invalid"


def test_empty_object_ref_allowlist_accepts_any_configured_bucket_key() -> (
    None
):
    """An empty allowlist is allow-all inside the configured bucket."""
    parsed = parse_research_input(
        'data: {"obs://dev-bucket/any/path/file.tsv": "hint"}',
        "dev-bucket",
    )

    assert parsed.candidates[0].comparison_key == (
        "obs://dev-bucket/any/path/file.tsv"
    )


def test_populated_object_ref_allowlist_rejects_unlisted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later whitelist still uses the same invalid-path failure."""
    _pin_object_ref_allowlist(monkeypatch, "obs://dev-bucket/listed.tsv")
    with pytest.raises(ResearchInputFailure) as caught:
        parse_research_input(
            'data: {"obs://dev-bucket/other.tsv": "hint"}',
            "dev-bucket",
        )

    assert caught.value.code == "research_dataset_path_invalid"


def test_populated_object_ref_allowlist_accepts_listed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later whitelist still accepts the listed configured-bucket key."""
    _pin_object_ref_allowlist(monkeypatch, "obs://dev-bucket/listed.tsv")
    parsed = parse_research_input(
        'data: {"obs://dev-bucket/listed.tsv": "hint"}',
        "dev-bucket",
    )

    assert parsed.candidates[0].comparison_key == (
        "obs://dev-bucket/listed.tsv"
    )


def test_malformed_explicit_data_block_does_not_fall_back_to_prose() -> None:
    """An associated data label selects the strict parser even when invalid."""
    query = "目标\ndata: {not valid json}"

    assert has_explicit_research_data_syntax(query, "dev-bucket") is True
    with pytest.raises(ResearchInputFailure, match="data block"):
        parse_research_input(query, "dev-bucket")


@pytest.mark.parametrize(
    "query",
    [
        "请讨论 obs://dev-bucket/a.tsv 的实验设计。",
        "data: 是一个常用的英文单词。",
        "路径可能是 /tmp/a.tsv，但不作为输入。",
    ],
)
def test_prose_without_approved_grammar_remains_untouched(query: str) -> None:
    """Path-like prose does not become a dataset through heuristic parsing."""
    parsed = parse_research_input(query, "dev-bucket")

    assert parsed.effective_query == query
    assert parsed.effective_to_original == tuple(range(len(query)))
    assert not parsed.removed_spans
    assert not parsed.candidates
    assert has_explicit_research_data_syntax(query, "dev-bucket") is False
