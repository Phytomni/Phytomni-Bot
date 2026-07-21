# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Citation and submit-error contracts for MCP result formatting."""

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    _normalize_citations,
    _reference_payload,
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
    assert text == "Evidence supports this hypothesis [1]."


def test_normalize_citations_captures_multi_index_with_prefix() -> None:
    """``[document: 1, 25]`` must capture both indices."""

    answer = "Both findings agree [document: 1, 25]."
    doc_list = [
        {"file_id": f"id{i}", "title": f"Paper {i}"} for i in range(1, 30)
    ]
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["id1", "id25"]
    assert text == "Both findings agree [1,2]."


def test_normalize_citations_dedups_distinct_indices_to_distinct_refs() -> (
    None
):
    """Two indices mapping to two distinct file_ids produce two refs.

    Pinning the minimum-correct dedup path: indices that map to
    distinct file_ids preserve as distinct ``[N]`` references in
    first-appearance order.
    """

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b]
    answer = "First [2] then [1]."
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-b", "paper-a"]
    assert text == "First [1] then [2]."


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
    assert text == "First [1] then [2] then [1]."


def test_normalize_citations_dedup_inside_multi_index_keeps_ref() -> None:
    """A multi-index bracket with a dedup hit keeps the existing ref."""

    paper_a = {"file_id": "paper-a", "title": "Paper A"}
    paper_b = {"file_id": "paper-b", "title": "Paper B"}
    doc_list = [paper_a, paper_b, paper_a]
    answer = "Multiple findings agree [1, 2, 3]."
    text, refs = _normalize_citations(answer, doc_list)

    assert [ref["file_id"] for ref in refs] == ["paper-a", "paper-b"]
    assert text == "Multiple findings agree [1,2,1]."


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
        "Claim [1] also [2] supported [1] cf [2] echoed [1] confirmed [2]."
    )


def test_reference_payload_projects_biblio_when_present() -> None:
    """Biblio fields present on doc are forwarded and .pdf is stripped."""
    doc = {
        "file_id": "f1",
        "title": "T.pdf",
        "au": "Smith J",
        "so": "Nature",
        "pm": "999",
    }
    payload = _reference_payload(doc)
    assert payload["file_id"] == "f1"
    assert payload["title"] == "T"
    assert payload["au"] == "Smith J"
    assert payload["so"] == "Nature"
    assert payload["pm"] == "999"


def test_reference_payload_falls_back_to_title_only() -> None:
    """Doc without biblio fields returns only file_id and title."""
    assert _reference_payload({"file_id": "f1", "title": "T"}) == {
        "file_id": "f1",
        "title": "T",
    }


def test_normalize_citations_carries_biblio_in_order() -> None:
    """Biblio fields survive _normalize_citations in first-appearance order."""
    answer = "First [2] then [1]."
    doc_list = [
        {"file_id": "a", "title": "A", "au": "AU-A"},
        {"file_id": "b", "title": "B", "au": "AU-B"},
    ]
    new_answer, refs = _normalize_citations(answer, doc_list)
    assert new_answer == "First [1] then [2]."
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
