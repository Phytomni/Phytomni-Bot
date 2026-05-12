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

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.client import call_tool

pytestmark = pytest.mark.live

WHEAT_DROUGHT_KEYWORDS = (
    "drought",
    "wheat",
    "triticum",
    "aba",
    "dreb",
    "snrk",
)


async def test_knowledge_agent_e2e_returns_evidence_backed_answer(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """KnowledgeAgent answers the wheat drought query with cue words.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("knowledge_agent.json")

    response = await call_tool(mcp_client, "KnowledgeAgent", payload)

    answer = response.formatted.answer
    assert answer, "KnowledgeAgent answer was empty"
    lowered = answer.lower()
    matched = [kw for kw in WHEAT_DROUGHT_KEYWORDS if kw in lowered]
    assert matched, (
        f"KnowledgeAgent answer missed every expected keyword "
        f"({WHEAT_DROUGHT_KEYWORDS}); got: {answer!r}"
    )


async def test_knowledge_agent_e2e_with_uploaded_brief(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
    published_demo_data: Dict[str, str],
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
