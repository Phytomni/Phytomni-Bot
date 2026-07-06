# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e tests for ``KnowledgeAgent`` over the stdio MCP client.

Covers two variants in one file:

* ``test_knowledge_agent_e2e_returns_evidence_backed_answer`` -- runs
  the committed ``knowledge_agent.json`` payload as-is (no file
  upload) and asserts the answer mentions at least one of the wheat
  drought-tolerance keywords.
* ``test_knowledge_agent_e2e_with_uploaded_brief`` -- injects the
  published brief PDF into ``obs_file_list`` and asserts the answer
  still ends up non-empty (the retrieval path with an uploaded doc is
  the second leg this suite needs to prove).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_knowledge_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live

# The uploaded-document leg drives the live retrieve -> rerank -> LLM
# fan-out with an extra source attached. When that backend stack is
# healthy a run completes in ~5 min; when it is degraded the same
# wrapper call does not return for 20-30+ min (observed: one success at
# 5.2 min, then 30/20/9 min and 5 consecutive 12 min runs all timing
# out, with `multi_retrieve_generate` itself never returning -- the
# MCP layer and this harness were ruled out). Skip by default so the
# suite stays deterministic; set PHYTOMNI_E2E_RUN_KA_UPLOAD=1 to
# exercise it once the rerank/LLM tier is back to single-digit-minute
# latency.
RUN_UPLOADED_KA_VAR = "PHYTOMNI_E2E_RUN_KA_UPLOAD"


async def test_knowledge_agent_e2e_returns_evidence_backed_answer(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """KnowledgeAgent answers the wheat drought query with cue words.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("knowledge_agent.json")

    response = await call_tool(mcp_client, "KnowledgeAgent", payload)

    assert_knowledge_answer(response.formatted.answer)


@pytest.mark.skipif(
    os.environ.get(RUN_UPLOADED_KA_VAR) != "1",
    reason=(
        "uploaded-document retrieve->rerank->LLM path is backend-bound "
        "and intermittently exceeds 20-30 min; set "
        "PHYTOMNI_E2E_RUN_KA_UPLOAD=1 to run when the tier is healthy"
    ),
)
async def test_knowledge_agent_e2e_with_uploaded_brief(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
    published_demo_data: dict[str, str],
) -> None:
    """KnowledgeAgent answers when the brief PDF is attached.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
        published_demo_data: Per-session OBS upload map.
    """
    payload = load_payload("knowledge_agent.json")
    payload["obs_file_list"] = [
        published_demo_data["docs/plant_science_brief.pdf"],
    ]

    response = await call_tool(mcp_client, "KnowledgeAgent", payload)

    assert (
        response.formatted.answer
    ), "KnowledgeAgent returned no answer when given the brief PDF"
