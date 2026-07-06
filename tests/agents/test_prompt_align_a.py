# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Structural tests pinning the Align-A citation contract across prompts.

Cited-agent prompts must declare their citation policy via
``## Citation Rules`` + a citable corpus under ``## Literature``;
prompts also receiving structural data add ``## Annotation Data``.
Pure JSON critics (``deep_research_review``) and citation-preserving
synthesizers (``deep_research_summary``) are intentionally outside
the parametrization.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.common.prompts import get_prompt
from mcp_server_phytomni.common.responses import assert_no_citation_residue

pytestmark = pytest.mark.unit

PROMPT_FILE = "src/mcp_server_phytomni/config/.prompts.yaml"

ALIGN_A_PROMPTS_WITH_STRUCTURAL = (
    "brief_gene_section_application",
    "brief_gene_section_cloning",
    "brief_gene_section_discovery",
    "brief_gene_section_functional",
    "brief_gene_introduction",
)

ALIGN_A_PROMPTS_LITERATURE_ONLY = (
    "retrieval",
    "retrieval_file",
    "deep_research_report",
)


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("name", ALIGN_A_PROMPTS_WITH_STRUCTURAL)
def test_section_or_intro_prompt_uses_align_a(name: str) -> None:
    """Section + intro prompts split annotation data from Literature."""
    body = get_prompt(PROMPT_FILE, f"user/{name}", {})
    assert (
        "## Citation Rules" in body
    ), f"{name} missing `## Citation Rules` header"
    assert "## Annotation Data" in body, (
        f"{name} missing `## Annotation Data` header — required by "
        f"Align-A so the LLM does not bracket-cite GO / InterPro / "
        f"Gene Structure / Homology context fields"
    )
    assert "Structural Annotations" not in body, (
        f"{name} reintroduced the `Structural Annotations` name the "
        f"LLM echoed as a `[Structural Annotations]` pseudo-citation; "
        f"the annotation data section must not carry a bracket-citable "
        f"name"
    )
    assert "## Literature" in body, f"{name} missing `## Literature` header"
    assert "[document:N]" in body, (
        f"{name} no longer instructs the [document:N] citation form the "
        f"repo uses internally (the post-processor renders [N] to the "
        f"client)"
    )
    assert "[document:X]" not in body, (
        f"{name} carries the placeholder [document:X] form — N must be a "
        f"real document number"
    )
    assert "## Reference Materials" not in body, (
        f"{name} still carries the legacy `## Reference Materials` "
        f"header — Align-A splits into `Structural Annotations` + "
        f"`Literature`"
    )


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("name", ALIGN_A_PROMPTS_LITERATURE_ONLY)
def test_literature_only_prompt_uses_align_a(name: str) -> None:
    """Knowledge / Review prompts declare the citation form.

    These prompts (retrieval / retrieval_file / deep_research_report)
    receive a retrieve_results blob in ``[document N begin] ... [document
    N end]`` shape and instruct the LLM to cite as ``[document:N]`` (the
    repo-internal form that aligns with that input); the post-processor
    deduplicates, sorts, and renumbers every marker to ``[N]`` for the
    client. The placeholder ``[document:X]`` form must never appear — N
    must always be a real document number.
    """
    body = get_prompt(PROMPT_FILE, f"user/{name}", {})
    assert "[document:N]" in body, (
        f"{name} no longer instructs the [document:N] citation form the "
        f"repo uses internally"
    )
    assert "[document:X]" not in body, (
        f"{name} carries the placeholder [document:X] form — N must be a "
        f"real document number"
    )


# --- e2e helper: citation-residue scanner ---


def test_assert_no_citation_residue_passes_on_clean_markdown() -> None:
    """A clean answer with only `[N]` markers passes."""

    answer = (
        "This is supported by [1] and [2]. Markdown link [text](url) "
        "is fine. Image ![alt](path) is fine. Reference link [label]: "
        "footer is fine."
    )
    assert_no_citation_residue(answer)  # raises on failure


def test_assert_no_citation_residue_fails_on_named_pseudo_citation() -> None:
    """A named bracket like `[document: InterPro]` triggers failure."""

    answer = "Domain analysis [document: InterPro] supports the claim."
    with pytest.raises(AssertionError, match="citation marker leaked"):
        assert_no_citation_residue(answer)


def test_assert_no_citation_residue_ignores_markdown_link_syntax() -> None:
    """`[label](url)` and `![alt](path)` are link / image syntax."""

    answer = (
        "See [the paper](https://example.com/p1) and the diagram "
        "![overview](./diagram.png)."
    )
    assert_no_citation_residue(answer)
