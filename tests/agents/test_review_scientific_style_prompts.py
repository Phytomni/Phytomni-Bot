# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pin Review's italic policy without asserting real model compliance."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.common.prompts import get_prompt

pytestmark = pytest.mark.unit

_PROMPT_FILE = "src/mcp_server_phytomni/config/.prompts.yaml"
_PROMPT_PARAMS = (
    (
        "deep_research_check",
        {"input_text": "<draft>", "source_docs_json": "[]"},
    ),
    (
        "deep_research_dimension",
        {"subtopic": "<topic>", "knowledge": "<knowledge>"},
    ),
    (
        "deep_research_feedback",
        {"existing_draft": "<draft>", "new_snippets": "<snippets>"},
    ),
    (
        "deep_research_summary",
        {
            "user_query": "<query>",
            "thesis": "<thesis>",
            "in_scope": "<scope>",
            "out_of_scope": "<excluded>",
            "subsections": "<sections>",
        },
    ),
)


@pytest.fixture(
    name="rendered_prompt", params=_PROMPT_PARAMS, ids=lambda case: case[0]
)
def rendered_review_prompt(request: pytest.FixtureRequest) -> str:
    """Exercise each actual load/render seam with harmless input sentinels."""
    name, parameters = request.param
    rendered = get_prompt(_PROMPT_FILE, f"user/{name}", parameters)
    assert "{{" not in rendered
    return " ".join(rendered.split())


def test_gene_italics_follow_source_context(rendered_prompt: str) -> None:
    """Require contextual symbols, not an identifier-shaped italic guess."""
    assert "gene and allele symbols" in rendered_prompt
    assert "source-supported" in rendered_prompt
    assert "species conventions" in rendered_prompt
    assert "distinguish gene/RNA from protein uses" in rendered_prompt
    assert "keep protein names upright" in rendered_prompt
    assert (
        "Preserve spelling and case; do not guess from identifier shape"
    ) in rendered_prompt


def test_taxonomic_italics_exclude_non_name_labels(
    rendered_prompt: str,
) -> None:
    """Separate scientific names from common names and botanical labels."""
    assert "Italicize Latin genus/species names" in rendered_prompt
    assert (
        "not common names, author abbreviations, strain/cultivar labels, "
        "or surrounding punctuation"
    ) in rendered_prompt


def test_rewrites_preserve_intended_markdown_italics(
    rendered_prompt: str,
) -> None:
    """Pin preservation through rewrites without changing scientific facts."""
    assert "Use Markdown italics" in rendered_prompt
    assert "Preserve appropriate existing Markdown italics" in rendered_prompt
    assert "through revision and assembly" in rendered_prompt
    assert (
        "Do not alter facts, numbers, or citation syntax for typography"
    ) in rendered_prompt


def test_in_silico_style_is_limited_to_scientific_prose(
    rendered_prompt: str,
) -> None:
    """Require prose emphasis while leaving machine-facing text unchanged."""
    assert "*in silico* in generated scientific prose" in rendered_prompt
    assert "do not reformat code identifiers, URLs, or filenames" in (
        rendered_prompt
    )
