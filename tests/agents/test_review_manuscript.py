# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for Review manuscript scrubbing after summary assembly."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.review.manuscript import (
    scrub_review_manuscript,
)

pytestmark = pytest.mark.unit

_IN_SCOPE = (
    "Identification, regulatory evidence, wax phenotypes, and breeding "
    "limits of the ZOS7-MYB60-CER1 pathway in upland rice."
)
_THESIS = (
    "The ZOS7-MYB60-CER1 regulatory pathway is proposed for drought "
    "resistance in upland rice."
)
_OUT = (
    "Human PPI methods, other drought resistance genes, and generic "
    "network analysis are out of scope."
)


def test_scrub_softens_overclaim_title_when_body_has_gaps() -> None:
    """A confers-title yields when the body says the link is untested."""
    text = (
        "### Title: The ZOS7-MYB60-CER1 Regulatory Pathway Confers "
        "Drought Resistance in Upland Rice\n\n"
        "### Abstract\n"
        "Direct evidence linking ZOS7 to CER1 regulation remains "
        "absent [document:2].\n"
    )
    out = scrub_review_manuscript(text, thesis=_THESIS)

    assert "Confers" not in out
    assert "Is Proposed for Drought Resistance" in out
    assert "[document:2]" in out


def test_scrub_drops_verbatim_in_scope_and_snippet_meta() -> None:
    """Planner scope strings and retrieval-meta sentences are removed."""
    text = (
        "### Introduction\n"
        f"Here we review {_IN_SCOPE} Human PPI methods are ignored.\n"
        "None of the supplied knowledge snippets mention CER1.\n"
        "Consequently, CER1 cannot be substantiated from the provided "
        "data.\n"
        "OsMYB60 binds the OsCER1 promoter [document:3].\n"
    )
    out = scrub_review_manuscript(
        text,
        thesis=_THESIS,
        in_scope=_IN_SCOPE,
        out_of_scope=_OUT,
    )

    assert _IN_SCOPE.rstrip(".") not in out
    assert "supplied knowledge snippets" not in out.lower()
    assert "provided data" not in out.lower()
    assert "OsMYB60 binds the OsCER1 promoter [document:3]." in out
    assert "Here we review ." not in out


def test_scrub_repairs_here_we_review_the_period() -> None:
    """A hole left after scope deletion does not stay as 'review the .'."""
    text = (
        "### Introduction\n"
        "Drought remains a bottleneck.\n"
        "Here we review the .\n"
        "OsMYB60 binds OsCER1 [document:3].\n"
    )
    out = scrub_review_manuscript(text)
    assert "Here we review the ." not in out
    assert "Here we review this topic." in out
    assert "[document:3]" in out


def test_scrub_rewrites_snippet_process_talk() -> None:
    """Dimension leftovers about snippets become scientific wording."""
    text = (
        "The supplied knowledge demonstrates that ZOS7 binds OsMYB60 "
        "[document:3]. Assays are not provided in these snippets. "
        "No CER1 link is present in the supplied documents.\n"
    )
    out = scrub_review_manuscript(text)
    assert "supplied knowledge" not in out.lower()
    assert "these snippets" not in out.lower()
    assert "supplied documents" not in out.lower()
    assert "ZOS7 binds OsMYB60 [document:3]." in out
    assert "not reported" in out


def test_scrub_drops_orphan_subheading_after_meta_removal() -> None:
    """A #### heading with no remaining body is removed."""
    text = (
        "### How identified\n"
        "ZOS7 is drought induced [document:1].\n\n"
        "#### Absence of Direct CER1 Evidence\n"
        "None of the supplied knowledge snippets mention CER1.\n\n"
        "### Next section\n"
        "Later evidence remains [document:3].\n"
    )
    out = scrub_review_manuscript(text)

    assert "Absence of Direct CER1 Evidence" not in out
    assert "### How identified" in out
    assert "[document:1]" in out
    assert "[document:3]" in out
