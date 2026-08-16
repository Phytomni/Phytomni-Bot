# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Citation and submit-error contracts for MCP result formatting."""

import pytest

from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_STATUS_KEY,
    CITATION_STATUS_LOOKUP_FAILED,
    CITATION_STATUS_MISSING,
)
from mcp_server_phytomni.mcp.formatting.cited import (
    clean_retrieval_title,
    format_authors,
    format_nature_citation,
)
from mcp_server_phytomni.mcp.result_formatting import (
    _normalize_citations,
    _reference_payload,
    build_tool_result_envelope,
    format_tool_result,
    is_cited_tool,
)

pytestmark = pytest.mark.server


def test_normalize_citations_captures_colon_space_digit() -> None:
    """Today's regex misses ``[document: 32]`` (colon + space + digit).

    The widened regex must capture it so the indexed doc_list chunk
    reaches ``references``.
    """

    answer = "Evidence supports this hypothesis [document: 32]."
    doc_list = [
        {"file_id": f"id{i}", "title": f"Paper {i}"} for i in range(1, 33)
    ]
    text, refs = _normalize_citations(answer, doc_list)

    assert len(refs) == 1
    assert refs[0]["file_id"] == "id32"
    assert refs[0]["title"] == "Paper 32"
    assert text == "Evidence supports this hypothesis <sup>1</sup>."


def test_normalize_citations_captures_multi_index_with_prefix() -> None:
    """``[document: 1, 25]`` must capture both indices."""

    answer = "Both findings agree [document: 1, 25]."
    doc_list = [
        {"file_id": f"id{i}", "title": f"Paper {i}"} for i in range(1, 30)
    ]
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["id1", "id25"]
    assert text == "Both findings agree <sup>1,2</sup>."


def test_normalize_citations_captures_repeated_document_word_prefixes() -> (
    None
):
    """Rewrite BriefGene ``[document 3, document 4]`` into superscripts."""

    answer = (
        "The locus is drought-linked "
        "[document 3, document 4, document 6] [Annotation data] "
        "[annotation: Homology context]."
    )
    doc_list = [
        {"file_id": f"id{i}", "title": f"Paper {i}"} for i in range(1, 7)
    ]
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["id3", "id4", "id6"]
    assert text == "The locus is drought-linked <sup>1-3</sup>."


def test_normalize_citations_dedups_distinct_indices_to_distinct_refs() -> (
    None
):
    """Two indices mapping to two distinct file_ids produce two refs.

    Pinning the minimum-correct dedup path: indices that map to
    distinct file_ids preserve as distinct superscript references in
    first-appearance order.
    """

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b]
    answer = "First [2] then [1]."
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-b", "paper-a"]
    assert text == "First <sup>1</sup> then <sup>2</sup>."


def test_normalize_citations_skips_named_pseudo_citations() -> None:
    """Named brackets stay verbatim — Align-A prompts forbid them upstream."""

    answer = (
        "See [document: InterPro] for domain notes and "
        "[document: Homology context] for orthologs."
    )
    doc_list = [{"file_id": "p1", "title": "Paper 1"}]
    text, refs = _normalize_citations(answer, doc_list)

    assert not refs
    assert text == answer


def test_normalize_citations_no_false_positive_on_prose() -> None:
    """Prose with letters + digit gap must not be captured.

    The widened regex still requires the digits to follow the
    letters/separator block immediately. ``[Note: see Chapter 4
    below]`` has intervening prose between the colon-space and the
    digit, so it stays verbatim.
    """

    answer = "[Note: see Chapter 4 below] is unrelated context."
    doc_list = [{"file_id": f"id{i}", "title": f"P{i}"} for i in range(1, 6)]
    text, refs = _normalize_citations(answer, doc_list)

    assert not refs
    assert text == answer


def test_normalize_citations_dedup_repoints_to_existing_ref() -> None:
    """A separate-bracket citation that dedups must re-point, not vanish.

    When the LLM cites three distinct indices in separate brackets and
    the third one maps to a file_id already cited by the first, the
    third citation must be rewritten to point at the existing
    reference number rather than dropping to an empty string. This
    pins the multi-chunk-same-paper retrieval pattern common in
    ``brief_gene`` (TOP_N >= 30) where one paper contributes multiple
    ranked chunks.
    """

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b, paper_a]
    answer = "First [1] then [2] then [3]."
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-a", "paper-b"]
    assert text == ("First <sup>1</sup> then <sup>2</sup> then <sup>1</sup>.")


def test_normalize_citations_dedup_inside_multi_index_keeps_ref() -> None:
    """A multi-index bracket deduplicates references after renumbering."""

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b, paper_a]
    answer = "Multiple findings agree [1, 2, 3]."
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-a", "paper-b"]
    assert text == "Multiple findings agree <sup>1,2</sup>."


def test_normalize_citations_multi_chunk_same_paper_pattern() -> None:
    """Brief_gene TOP_N=30 pattern: many chunks dedup to a few papers."""

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b, paper_a, paper_b, paper_a, paper_b]
    answer = (
        "Claim [1] also [2] supported [3] cf [4] echoed [5] confirmed [6]."
    )
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-a", "paper-b"]
    assert text == (
        "Claim <sup>1</sup> also <sup>2</sup> supported <sup>1</sup> "
        "cf <sup>2</sup> echoed <sup>1</sup> confirmed <sup>2</sup>."
    )


def test_normalize_citations_sorts_deduplicated_numbers() -> None:
    """Each marker sorts unique numbers after first-appearance renumbering."""
    docs = [
        {"file_id": str(index), "title": str(index)} for index in range(1, 4)
    ]

    text, refs = _normalize_citations(
        "Seed [3]. Claim [2,3,1,2].",
        docs,
    )

    assert [ref["file_id"] for ref in refs] == ["3", "2", "1"]
    assert text == "Seed <sup>1</sup>. Claim <sup>1-3</sup>."


def test_normalize_citations_merges_adjacent_markers_into_range() -> None:
    """Adjacent source markers render as one compact superscript range."""
    docs = [
        {"file_id": str(index), "title": str(index)} for index in range(1, 5)
    ]

    text, refs = _normalize_citations("Claim [1][2][3][4].", docs)

    assert [ref["file_id"] for ref in refs] == ["1", "2", "3", "4"]
    assert text == "Claim <sup>1-4</sup>."


def test_normalize_citations_keeps_position_and_compacts_mixed_ranges() -> (
    None
):
    """Superscripts stay authored while mixed runs use compact ranges."""
    docs = [{"file_id": str(index), "title": str(index)} for index in range(5)]

    no_move, _ = _normalize_citations("claim[1].", docs)
    compacted, _ = _normalize_citations(
        "seed [1][2][3][4][5]\nclaim [1,2,3,5]", docs
    )

    assert no_move == "claim<sup>1</sup>."
    assert compacted.splitlines()[-1] == "claim <sup>1-3,5</sup>"


def test_normalize_citations_removes_invalid_only_marker() -> None:
    """An invalid-only marker disappears without an empty superscript."""
    text, refs = _normalize_citations(
        "Unsupported [document:9] remains usable.",
        [{"file_id": "f1", "title": "Paper"}],
    )

    assert text == "Unsupported  remains usable."
    assert not refs
    assert "<sup></sup>" not in text


@pytest.mark.parametrize(
    "suffix",
    (
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".rtf",
        ".txt",
        ".md",
        ".html",
        ".htm",
        ".msg",
        ".eml",
    ),
)
def test_clean_retrieval_title_strips_approved_suffixes(suffix: str) -> None:
    """Approved retrieval file suffixes are removed case-insensitively."""
    assert (
        clean_retrieval_title(f"Publication{suffix.upper()}") == "Publication"
    )


@pytest.mark.parametrize(
    "title",
    (
        "Magnaporthe.oryzae",
        "Escherichia.coli",
        "release v1.2",
        "doi 10.1000/article.pdfx",
        "Publication.unknown",
        ".PDF",
    ),
)
def test_clean_retrieval_title_preserves_non_file_endings(title: str) -> None:
    """Unknown, scientific, decimal, DOI-like, and suffix-only titles stay."""
    assert clean_retrieval_title(title) == title


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("Taylor, NL", "Taylor, N. L."),
        ("Taylor, NL; Millar, AH", "Taylor, N. L. & Millar, A. H."),
        (
            "de la Cruz, JY; O'Neil, A-B; Wang, Q; Li, X; Kim, S",
            "de la Cruz, J. Y., O'Neil, A.-B., Wang, Q., Li, X. & Kim, S.",
        ),
        ("A, A; B, B; C, C; D, D; E, E; F, F", "A, A. et al."),
        ("Smith, J, Jr", "Smith, J. Jr"),
        ("Smith, J, Sr", "Smith, J. Sr"),
        ("Smith, J, II", "Smith, J. II"),
        ("Smith, J, III", "Smith, J. III"),
        ("Smith, J, IV", "Smith, J. IV"),
        ("Consortium Name", "Consortium Name"),
        ("van der Waals, JW", "van der Waals, J. W."),
        ("Dvořák, JY", "Dvořák, J. Y."),
        ("Taylor, J-Y", "Taylor, J.-Y."),
        ("Taylor, NL;; Millar, AH", "Taylor, N. L. & Millar, A. H."),
        ("Taylor, john", "Taylor, john"),
        ("Taylor, John", "Taylor, John"),
        ("Smith, J, PhD", "Smith, J, PhD"),
        ("Smith, J, Jr, Extra", "Smith, J, Jr, Extra"),
    ),
)
def test_format_authors_is_conservative(raw: str, expected: str) -> None:
    """Only the approved semicolon/surname/initial grammar is reformatted."""
    assert format_authors(raw) == expected


def test_format_nature_citation_complete_record() -> None:
    """A complete record has one deterministic Nature-style line."""
    expected = (
        "Taylor, N. L. & Millar, A. H. "
        "Plant Mitochondrial Proteomics. "
        "*PLANT MITOCHONDRIA: METHODS AND PROTOCOLS* "
        "**1305,** 83–106 (2015). "
        "[https://doi.org/10.1007/978-1-4939-2639-8_6]"
        "(https://doi.org/10.1007/978-1-4939-2639-8_6)"
    )
    doc = {
        "au": "Taylor, NL; Millar, AH",
        "ti": "Plant Mitochondrial Proteomics",
        "so": "PLANT MITOCHONDRIA: METHODS AND PROTOCOLS",
        "vl": "1305",
        "bp": "83",
        "ep": "106",
        "py": "2015",
        "di": "10.1007/978-1-4939-2639-8_6",
    }

    assert format_nature_citation(doc, "retrieval title") == expected


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    (
        ({"vl": "7", "bp": "12", "ep": "19"}, "**7,** 12–19."),
        ({"vl": "7", "bp": "12", "ep": "12"}, "**7,** 12."),
        ({"vl": "7", "bp": "12"}, "**7,** 12."),
        ({"vl": "7", "ep": "19"}, "**7,** 19."),
        ({"vl": "7", "ar": "e123"}, "**7,** e123."),
        ({"vl": "7"}, "**7**."),
        ({"py": "2026"}, "(2026)."),
    ),
)
def test_format_nature_citation_partial_locators(
    doc: dict[str, str], expected_fragment: str
) -> None:
    """Partial records omit empty separators and choose pages before ar."""
    assert expected_fragment in format_nature_citation(doc, "Title")


def test_format_nature_citation_avoids_duplicate_terminal_punctuation() -> (
    None
):
    """Author/title punctuation is not duplicated and no empty year appears."""
    citation = format_nature_citation(
        {"au": "Consortium.", "ti": "Question?", "so": "Journal!"},
        "fallback",
    )

    assert citation.startswith(r"Consortium. Question? *Journal\!*.")
    assert ".." not in citation
    assert "()" not in citation
    assert ",." not in citation


@pytest.mark.parametrize(
    "doi",
    (
        "10.1000/x",
        "doi: 10.1000/x",
        "https://doi.org/10.1000/x",
        "http://dx.doi.org/10.1000/x",
    ),
)
def test_reference_payload_accepts_approved_doi_forms(doi: str) -> None:
    """Every approved DI form projects one canonical DOI link."""
    payload = _reference_payload({"file_id": "f1", "title": "T", "di": doi})

    assert payload["formatted_citation"].endswith(
        "[https://doi.org/10.1000/x](https://doi.org/10.1000/x)"
    )
    assert "doi_missing" not in payload


def test_reference_payload_encodes_doi_target_only() -> None:
    """Visible canonical DOI remains readable while the target is encoded."""
    payload = _reference_payload({"title": "T", "di": "10.1000/a(b)?c"})

    assert payload["formatted_citation"].endswith(
        "[https://doi.org/10.1000/a(b)?c]"
        "(https://doi.org/10.1000/a%28b%29%3Fc)"
    )


def test_reference_payload_rejects_doi_visible_label_injection() -> None:
    """A DOI cannot terminate its owned Markdown link label."""
    payload = _reference_payload(
        {"title": "T", "di": "10.1000/x](https://evil.test)"}
    )

    assert payload["formatted_citation"] == "T."
    assert payload["doi_missing"] is True
    assert "evil.test" not in payload["formatted_citation"]


def test_reference_payload_uses_only_valid_doi_host_dl_fallback() -> None:
    """DL is eligible only when DI is absent and DL is a resolver URL."""
    valid = _reference_payload(
        {"title": "T", "dl": "https://doi.org/10.1000/from-dl"}
    )
    invalid_di = _reference_payload(
        {
            "title": "T",
            "di": "invalid",
            "dl": "https://doi.org/10.1000/not-used",
        }
    )
    invalid_links = [
        _reference_payload({"title": "T", "dl": value})
        for value in (
            "10.1000/bare",
            "https://example.com/10.1000/x",
            "ftp://doi.org/10.1000/x",
        )
    ]

    assert "10.1000/from-dl" in valid["formatted_citation"]
    assert invalid_di["doi_missing"] is True
    assert "not-used" not in invalid_di["formatted_citation"]
    assert all(payload["doi_missing"] is True for payload in invalid_links)


def test_reference_payload_projects_biblio_and_cleaned_title() -> None:
    """Structured fields remain while the display excludes file_id."""
    doc = {
        "file_id": "f1",
        "title": "T.PDF",
        "au": "Smith, J",
        "so": "Nature",
        "pm": "999",
    }
    payload = _reference_payload(doc)
    assert payload["file_id"] == "f1"
    assert payload["title"] == "T"
    assert payload["au"] == "Smith, J"
    assert payload["so"] == "Nature"
    assert payload["pm"] == "999"
    assert "formatted_citation" in payload
    assert "f1" not in payload["formatted_citation"]
    assert payload["doi_missing"] is True


def test_reference_payload_falls_back_to_title_only() -> None:
    """A doc without bibliography returns a display title and DOI flag."""
    assert _reference_payload({"file_id": "f1", "title": "T"}) == {
        "file_id": "f1",
        "title": "T",
        "formatted_citation": "T.",
        "doi_missing": True,
    }


def test_reference_payload_escapes_untrusted_metadata() -> None:
    """Source fields cannot create Markdown or HTML beyond owned markup."""
    payload = _reference_payload(
        {
            "title": "fallback<script>.pdf",
            "au": "<b>Smith</b>, J",
            "ti": "Title [link](https://evil.test) <img>",
            "so": "*Journal* <script>",
            "vl": "**7**",
            "bp": "[1]",
            "py": "<2026>",
        }
    )
    citation = payload["formatted_citation"]

    assert "<script>" not in citation
    assert "<img>" not in citation
    assert "https://evil.test" in citation
    assert r"\[link\]\(https://evil.test\)" in citation
    assert r"\*Journal\*" in citation
    assert r"\*\*7\*\*" in citation


def test_reference_payload_parses_authors_before_escaping_entities() -> None:
    """Escaped entities cannot become author delimiters."""
    payload = _reference_payload(
        {
            "title": "fallback",
            "au": "Research & Development, AB; Smith, J",
            "ti": "A title",
        }
    )

    citation = payload["formatted_citation"]
    assert citation.startswith(
        "Research &amp; Development, A. B. & Smith, J. A title."
    )
    assert "amp &" not in citation


def test_reference_payload_flattens_and_escapes_multiline_sources() -> None:
    """Untrusted source metadata remains one escaped citation line."""
    payload = _reference_payload(
        {
            "title": "fallback",
            "au": "Smith, J\n# heading; Consortium *Name*",
            "ti": "Title\n[link](https://evil.test)",
            "so": "Journal\r\n# injected | table",
            "vl": "7\n- list",
        }
    )

    citation = payload["formatted_citation"]
    assert "\n" not in citation
    assert "\r" not in citation
    assert r"\# heading" in citation
    assert r"\[link\]\(https://evil.test\)" in citation
    assert r"\# injected \| table" in citation
    assert r"\*Name\*" in citation


def test_reference_payload_replaces_c0_and_c1_source_controls() -> None:
    """Public citation text replaces C0/C1 controls with safe spacing."""
    payload = _reference_payload(
        {
            "title": "fallback",
            "au": "Smith, J\x00; Jones, A\x1b",
            "ti": "Control\x00Title\x1bSafe\u0085Text",
            "so": "Journal\x7fName",
        }
    )

    citation = payload["formatted_citation"]
    assert citation == (
        "Smith, J. & Jones, A. Control Title Safe Text. *Journal Name*."
    )
    assert not any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in citation
    )


@pytest.mark.parametrize(
    "status", (CITATION_STATUS_MISSING, CITATION_STATUS_LOOKUP_FAILED)
)
def test_missing_status_forces_clean_title_only(status: str) -> None:
    """Metadata misses ignore stale bibliography and expose no private key."""
    payload = _reference_payload(
        {
            "file_id": "f1",
            "title": "retrieval [title].PDF",
            "au": "Stale, S",
            "ti": "Stale publication",
            "di": "10.1000/stale",
            CITATION_STATUS_KEY: status,
        }
    )

    assert payload["formatted_citation"] == r"retrieval \[title\]"
    assert payload["doi_missing"] is True
    assert CITATION_STATUS_KEY not in payload
    assert payload["ti"] == "Stale publication"


def test_selected_metadata_miss_sets_degradation_and_sanitizes_raw() -> None:
    """Only selected miss status projects degradation and never debug raw."""
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Selected [1].",
                    "doc_list": [
                        {
                            "file_id": "f1",
                            "title": "Selected.pdf",
                            CITATION_STATUS_KEY: CITATION_STATUS_MISSING,
                        },
                        {
                            "file_id": "f2",
                            "title": "Uncited.pdf",
                            CITATION_STATUS_KEY: CITATION_STATUS_MISSING,
                        },
                    ],
                }
            }
        ]
    }

    envelope = build_tool_result_envelope("KnowledgeAgent", payload)

    assert envelope.formatted.metadata["citation_metadata_degraded"] is True
    assert envelope.formatted.answer == "Selected <sup>1</sup>."
    assert envelope.formatted.references[0]["formatted_citation"] == "Selected"
    assert CITATION_STATUS_KEY not in str(envelope.raw)


def test_uncited_miss_and_no_id_fallback_do_not_degrade() -> None:
    """Only selected keyed misses establish database degradation."""
    uncited = format_tool_result(
        "KnowledgeAgent",
        {
            "choices": [
                {
                    "message": {
                        "content": "No references selected.",
                        "doc_list": [
                            {
                                "file_id": "f1",
                                "title": "Uncited",
                                CITATION_STATUS_KEY: CITATION_STATUS_MISSING,
                            }
                        ],
                    }
                }
            ]
        },
    )
    no_id = format_tool_result(
        "KnowledgeAgent",
        {
            "choices": [
                {
                    "message": {
                        "content": "Fallback [1].",
                        "doc_list": [{"title": "No ID"}],
                    }
                }
            ]
        },
    )

    assert "citation_metadata_degraded" not in uncited.metadata
    assert "citation_metadata_degraded" not in no_id.metadata


def test_missing_doi_does_not_imply_database_degradation() -> None:
    """DOI quality is separate from citation database availability."""
    result = format_tool_result(
        "KnowledgeAgent",
        {
            "choices": [
                {
                    "message": {
                        "content": "Evidence [1].",
                        "doc_list": [{"file_id": "f1", "title": "T"}],
                    }
                }
            ]
        },
    )

    assert result.references[0]["doi_missing"] is True
    assert "citation_metadata_degraded" not in result.metadata


def test_normalize_citations_carries_biblio_in_order() -> None:
    """Biblio fields survive _normalize_citations in first-appearance order."""
    answer = "First [2] then [1]."
    doc_list = [
        {"file_id": "a", "title": "A", "au": "AU-A"},
        {"file_id": "b", "title": "B", "au": "AU-B"},
    ]
    new_answer, refs = _normalize_citations(answer, doc_list)
    assert new_answer == "First <sup>1</sup> then <sup>2</sup>."
    assert refs[0]["file_id"] == "b" and refs[0]["au"] == "AU-B"
    assert refs[1]["file_id"] == "a" and refs[1]["au"] == "AU-A"


def test_is_cited_tool() -> None:
    """is_cited_tool returns True for cited agents and False for others."""
    assert is_cited_tool("KnowledgeAgent")
    assert is_cited_tool("ReviewAgent")
    assert is_cited_tool("BriefGeneAgent")
    assert is_cited_tool("KnowledgeAgents")
    assert not is_cited_tool("DeepGenomeAgent")
    assert not is_cited_tool("ChatAgent")


def test_analyst_result_rejects_whitespace_task_id() -> None:
    """Whitespace-only task_id is treated as a missing submit id."""
    result = format_tool_result(
        "AnalystAgent",
        {
            "task_id": "   ",
            "output_dir": "/obs/out",
            "compute_resource": "small",
        },
    )
    assert result.answer == "Task submission failed: missing task_id"
    assert result.metadata["status"] == "FAILED"


def test_analyst_result_rejects_missing_task_id() -> None:
    """A None task_id must not look like a successful submit."""
    result = format_tool_result(
        "AnalystAgent",
        {
            "task_id": None,
            "output_dir": "/obs/out",
            "compute_resource": "small",
        },
    )
    assert result.answer == "Task submission failed: missing task_id"
    assert result.metadata["status"] == "FAILED"
    assert result.metadata["log_status"] == "sync_failed"
    assert result.metadata["task_id"] is None


def test_gene_network_result_rejects_missing_task_id() -> None:
    """Nested network_task with null task_id is a failed submit."""
    result = format_tool_result(
        "GeneNetworkAgent",
        {
            "network_task": {
                "task_id": None,
                "output_dir": "/obs/net",
                "compute_resource": "medium",
            },
            "phytomni_state": {"goal_description": "x"},
        },
    )
    assert result.answer == "Task submission failed: missing task_id"
    assert result.metadata["status"] == "FAILED"
    assert result.metadata["log_status"] == "sync_failed"


def test_deep_genome_result_rejects_missing_task_id() -> None:
    """Empty DeepGenome umbrella id is FAILED, not success."""
    result = format_tool_result(
        "DeepGenomeAgent",
        {
            "task_id": None,
            "output_dir": "/tmp/x",
            "compute_resource": "deep-genome",
        },
        arguments={"species_code": "ath", "gene_id": "AT1G01010"},
    )
    assert result.answer == "Task submission failed: missing task_id"
    assert result.metadata["status"] == "FAILED"
    assert result.metadata["log_status"] == "sync_failed"


def test_in_silico_result_rejects_empty_task_ids() -> None:
    """No submitted child ids must not print a success message."""
    result = format_tool_result(
        "InSilicoResearchAgent",
        {
            "task_ids": {},
            "goals": [{"goal": "g", "context": "c"}],
            "output_dir": "/obs/r",
            "error": "download failed",
            "failures": [],
        },
    )
    assert "successfully" not in result.answer.lower()
    assert result.metadata["status"] == "FAILED"
    assert result.metadata["log_status"] == "sync_failed"
    assert result.metadata["error"] == "download failed"
