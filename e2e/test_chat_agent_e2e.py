# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``ChatAgent`` over the stdio MCP client.

Submits the committed ``demo_data/payloads/chat_agent.json`` payload
through ``PhytomniMcpClient`` and asserts the returned answer mentions
at least one canonical photosynthesis keyword. Runs only when the
suite is invoked manually with a configured ``.env``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.client import call_tool

pytestmark = pytest.mark.live

PHOTOSYNTHESIS_KEYWORDS = ("photosynthesis", "c3", "calvin", "rubisco")


async def test_chat_agent_e2e_returns_photosynthesis_answer(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """ChatAgent answers the C3 query with photosynthesis-related text.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("chat_agent.json")

    response = await call_tool(mcp_client, "ChatAgent", payload)

    answer = response.formatted.answer
    assert answer, "ChatAgent answer was empty"
    lowered = answer.lower()
    matched = [kw for kw in PHOTOSYNTHESIS_KEYWORDS if kw in lowered]
    assert matched, (
        f"ChatAgent answer missed every expected keyword "
        f"({PHOTOSYNTHESIS_KEYWORDS}); got: {answer!r}"
    )
