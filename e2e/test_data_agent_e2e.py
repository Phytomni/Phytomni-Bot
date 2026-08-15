# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``DataAgent`` over the stdio MCP client.

Submits the committed ``data_agent.json`` payload (an NL2SQL request
for the Os01g0177400 rice transcript ID) and asserts the formatted
answer contains either a SQL-looking statement or a structured result
fragment, so the regression catches both blank responses and
free-text-only completions that bypass the SQL backend.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest
from scripts.dataagent_root_cause_probe import (
    INCIDENT_DIALOGUE_ID,
    build_incident_payload,
)

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_data_answer
from .helpers.client import call_tool

pytestmark = pytest.mark.live


async def test_data_agent_e2e_returns_nl2sql_response(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """DataAgent answers the Os01g0177400 transcript-ID query.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("data_agent.json")

    response = await call_tool(mcp_client, "DataAgent", payload)

    assert_data_answer(response.formatted.answer)


@pytest.mark.skipif(
    not all(
        os.environ.get(flag) == "1"
        for flag in ("PHYTOMNI_RUN_INTEGRATION", "PHYTOMNI_ALLOW_NETWORK")
    ),
    reason=(
        "exact DataAgent replay requires explicit integration and network "
        "guards"
    ),
)
async def test_data_agent_exact_cdna_query_e2e(
    mcp_client: PhytomniMcpClient,
) -> None:
    """Run the fixed incident query without asserting unverified sequence."""
    payload = build_incident_payload()
    assert payload["dialogue_id"] == INCIDENT_DIALOGUE_ID
    response = await call_tool(
        mcp_client,
        "DataAgent",
        {"user_query": payload["arguments"]["user_query"]},
    )
    assert response.formatted.answer.strip()
