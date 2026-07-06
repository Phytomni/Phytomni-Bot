# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``DataAgent`` over the stdio MCP client.

Submits the committed ``data_agent.json`` payload (an NL2SQL homology
question about Os01g0177400 orthologs in wheat) and asserts the formatted
answer contains either a SQL-looking statement or a structured result
fragment, so the regression catches both blank responses and
free-text-only completions that bypass the SQL backend.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_data_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live


async def test_data_agent_e2e_returns_nl2sql_response(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """DataAgent answers the Os01g0177400 homology query.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("data_agent.json")

    response = await call_tool(mcp_client, "DataAgent", payload)

    assert_data_answer(response.formatted.answer)
