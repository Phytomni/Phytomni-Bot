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

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_review_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live


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

    assert_review_answer(response.formatted.answer)
