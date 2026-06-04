# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Structural tests pinning the Align-A citation contract across prompts.

Cited-agent prompts must declare their citation policy via
``## Citation Rules`` + a citable corpus under ``## Literature``;
prompts also receiving structural data add ``## Structural
Annotations``. Pure JSON critics (``deep_research_review``) and
citation-preserving synthesizers (``deep_research_summary``) are
intentionally outside the parametrization.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.common.prompts import get_prompt

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


@pytest.mark.parametrize("name", ALIGN_A_PROMPTS_WITH_STRUCTURAL)
def test_section_or_intro_prompt_uses_align_a(name: str) -> None:
    """Section + intro prompts split Structural Annotations from Literature."""
    body = get_prompt(PROMPT_FILE, f"user/{name}", {})
    assert (
        "## Citation Rules" in body
    ), f"{name} missing `## Citation Rules` header"
    assert "## Structural Annotations" in body, (
        f"{name} missing `## Structural Annotations` header — "
        f"required by Align-A so the LLM does not bracket-cite GO / "
        f"InterPro / Gene Structure / Homology context fields"
    )
    assert "## Literature" in body, f"{name} missing `## Literature` header"
    assert "[document:X] verbatim" not in body, (
        f"{name} still carries the legacy `[document:X] verbatim` "
        f"rule — Align-A says cite as `[N]` only"
    )
    assert "## Reference Materials" not in body, (
        f"{name} still carries the legacy `## Reference Materials` "
        f"header — Align-A splits into `Structural Annotations` + "
        f"`Literature`"
    )


@pytest.mark.parametrize("name", ALIGN_A_PROMPTS_LITERATURE_ONLY)
def test_literature_only_prompt_uses_align_a(name: str) -> None:
    """Knowledge / Review prompts declare Align-A citation form.

    These prompts (retrieval / retrieval_file / deep_research_report)
    receive a retrieve_results blob in ``[document N begin] ... [document
    N end]`` shape and must instruct the LLM to cite as ``[N]`` only.
    Any lingering ``[document:X]`` instruction text invites the LLM to
    drift to the colon-prefixed form (which Task 1's widened regex
    now captures, but the prompt should still pin the convention).
    """
    body = get_prompt(PROMPT_FILE, f"user/{name}", {})
    assert "[document:X]" not in body, (
        f"{name} still references `[document:X]` form in its rules "
        f"— Align-A says cite as `[N]` only"
    )
