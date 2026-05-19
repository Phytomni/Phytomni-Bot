# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``BriefGeneAgent`` over the stdio MCP client.

Submits the committed ``brief_gene_agent.json`` payload (the single
locus AT1G01010) and asserts the formatted answer mentions the gene
identifier together with at least one of the canonical gene-card
sections (function/expression/orthology/literature), so the regression
catches both empty responses and free-form prose that bypassed the
gene-annotation lookup.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import ANNOTATION_CUES, GENE_ID
from .helpers.client import call_tool

pytestmark = pytest.mark.live


async def test_brief_gene_agent_e2e_returns_gene_card(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """BriefGeneAgent returns a gene-card for AT1G01010.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("brief_gene_agent.json")

    response = await call_tool(mcp_client, "BriefGeneAgent", payload)

    answer = response.formatted.answer
    assert answer, "BriefGeneAgent answer was empty"
    lowered = answer.lower()
    assert GENE_ID.lower() in lowered, (
        f"BriefGeneAgent answer did not mention {GENE_ID}; " f"got: {answer!r}"
    )
    matched = [cue for cue in ANNOTATION_CUES if cue in lowered]
    assert matched, (
        f"BriefGeneAgent answer lacked every annotation cue "
        f"({ANNOTATION_CUES}); got: {answer!r}"
    )
