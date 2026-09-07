# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pin Review's citation-diversity instructions, not model compliance."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.common.prompts import get_prompt

pytestmark = pytest.mark.unit

_PROMPT_FILE = "src/mcp_server_phytomni/config/.prompts.yaml"


def _render_prompt(name: str, parameters: dict[str, str]) -> str:
    """Render the tracked prompt with supplied inputs and normalize spacing."""
    rendered = get_prompt(_PROMPT_FILE, f"user/{name}", parameters)
    assert "{{" not in rendered
    return " ".join(rendered.split())


def test_dimension_synthesizes_only_relevant_independent_sources() -> None:
    """Require synthesis without padding single-source paragraphs."""
    prompt = _render_prompt(
        "deep_research_dimension",
        {"subtopic": "Rice wax", "knowledge": "<evidence>"},
    )
    assert "three or more factual sentences" in prompt
    assert "at least two relevant source documents are available" in prompt
    assert "at least two distinct document IDs" in prompt
    assert "Do not write more than two consecutive factual sentences" in prompt
    assert "If only one document genuinely supports a point" in prompt
    assert "at most two factual sentences" in prompt
    assert "Never add an unrelated citation" in prompt
    assert "takes precedence over the 80-150-word target" in prompt
    assert "Correlation is not causation" in prompt
    assert "Do not create new citation tags" in prompt


def test_critic_flags_concentration_without_relaxing_scope() -> None:
    """Independent-evidence searches keep the existing off-topic gate."""
    prompt = _render_prompt(
        "deep_research_review",
        {
            "current_subtopic": "Rice wax",
            "other_subtopics": "Breeding limits",
            "draft_text": "<draft>",
        },
    )
    assert "three or more factual sentences" in prompt
    assert "one unique document ID" in prompt
    assert "citation-concentration risk" in prompt
    assert 'Set "has_critical_gaps" to true' in prompt
    assert "focused searches for independent corroboration" in prompt
    assert "if none is likely available" in prompt
    assert '"off_topic": false' in prompt
    assert '"drop_doc_ids": []' in prompt
    assert "leave search_queries empty" in prompt
    assert "inside the crop and gene names of the current subtopic" in prompt
    assert "Use at most three focused search queries" in prompt


def test_feedback_handles_available_and_missing_independent_evidence() -> None:
    """Permit compression without permitting unsupported factual revisions."""
    prompt = _render_prompt(
        "deep_research_feedback",
        {"existing_draft": "<draft>", "new_snippets": "<new evidence>"},
    )
    assert "When new snippets provide independent evidence" in prompt
    assert "genuine multi-source synthesis" in prompt
    assert "do not merely append a new citation to unchanged claims" in prompt
    assert "If no independent new evidence supports a flagged paragraph" in (
        prompt
    )
    assert "compress it to at most two factual sentences" in prompt
    assert "instead of adding an unrelated citation" in prompt
    assert "Change factual claims only when" in prompt
    assert "Keep the original scope and the claim-led tone" in prompt
    assert "Ignore human drug-target, viral, or C60 snippets" in prompt
    assert "Preserve existing valid citations" in prompt


def test_summary_shortens_paragraphs_without_losing_audited_evidence() -> None:
    """Sentence-boundary splits preserve factual and citation integrity."""
    prompt = _render_prompt(
        "deep_research_summary",
        {
            "user_query": "Rice wax",
            "thesis": "<thesis>",
            "in_scope": "<scope>",
            "out_of_scope": "<excluded>",
            "subsections": "<sections>",
        },
    )
    assert "Preserve paragraph boundaries" in prompt
    assert "three or more factual sentences and only one document ID" in prompt
    assert "splitting at existing sentence boundaries" in prompt
    assert "at most two factual sentences per paragraph" in prompt
    assert (
        "Keep every audited sentence and its attached citations unchanged"
        in (prompt)
    )
    assert "Never move or add citations merely" in prompt
    assert "### Abstract" in prompt
    assert "### Introduction" in prompt
    assert "### Conclusions" in prompt
    assert "Do not force two subheadings" in prompt
    assert "<sections>" in prompt
