# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``ReviewAgent`` over the stdio MCP client.

Submits the committed ``review_agent.json`` payload (a sorghum drought
review request) and asserts the resulting answer contains at least
three top-level markdown section headers, which is the report shape
the agent's prompt template targets and the easiest signal that the
multi-section drafting path ran end-to-end.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.client import call_tool

pytestmark = pytest.mark.live

MIN_REVIEW_SECTIONS = 3


def _markdown_body(answer: str) -> str:
    """Return the markdown body from a possibly JSON-wrapped answer.

    Citation-bearing agents serialize ``answer`` as a JSON envelope
    ``{"content": "<markdown>", "doc_list": [...]}`` per the documented
    ``FormattedToolResult`` contract, so the markdown (with its
    section headers) lives inside the escaped ``content`` value. Parse
    that out when present; fall back to the raw string for the
    plain-markdown path (e.g. ChatAgent).

    Args:
        answer: Raw ``response.formatted.answer`` string.

    Returns:
        The markdown body to scan for section headers.
    """
    try:
        parsed = json.loads(answer)
    except (ValueError, TypeError):
        return answer
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        return parsed["content"]
    return answer


async def test_review_agent_e2e_returns_multi_section_review(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """ReviewAgent produces a multi-section sorghum drought review.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("review_agent.json")

    response = await call_tool(mcp_client, "ReviewAgent", payload)

    answer = response.formatted.answer
    assert answer, "ReviewAgent answer was empty"
    body = _markdown_body(answer)
    sections = [line for line in body.splitlines() if line.startswith("## ")]
    assert len(sections) >= MIN_REVIEW_SECTIONS, (
        f"ReviewAgent answer had only {len(sections)} `## ` sections "
        f"(expected >= {MIN_REVIEW_SECTIONS}); got: {answer!r}"
    )
