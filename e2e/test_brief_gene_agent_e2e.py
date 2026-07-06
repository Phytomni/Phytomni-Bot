# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``BriefGeneAgent`` over the stdio MCP client.

Submits the committed ``brief_gene_agent.json`` payload (the single
rice locus Os01g0177400, Oryza sativa) and asserts the formatted
answer mentions the gene identifier together with the rich preamble
structure (``## Gene Profiles`` + all four numbered analytical
sections), so the regression catches empty responses, free-form prose
that bypassed the gene-annotation lookup, and a silently-dropped
section node.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_brief_gene_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live


async def test_brief_gene_agent_e2e_returns_rich_preamble(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """BriefGeneAgent returns the rich gene preamble for Os01g0177400.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("brief_gene_agent.json")

    response = await call_tool(mcp_client, "BriefGeneAgent", payload)

    assert_brief_gene_answer(response.formatted.answer)
