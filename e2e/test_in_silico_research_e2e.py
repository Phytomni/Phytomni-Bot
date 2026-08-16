# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``InSilicoResearchAgent`` with task polling.

Submits the committed in_silico_research_agent.json payload (decompose
the plant-science brief PDF into reproducibility tasks) through the
stdio MCP client, and currently accepts a live remote ``RUNNING``
verdict as ``ACCEPTED_WITH_GAPS`` because research jobs are 1h-48h.
A success terminal still requires the published output directory.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_remote_running_or_success
from .helpers.polling import submit_and_poll_to_remote_running

pytestmark = pytest.mark.live


async def test_in_silico_research_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """InSilicoResearchAgent submits and accepts remote RUNNING with gaps.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("in_silico_research_agent.json")

    state = await submit_and_poll_to_remote_running(
        mcp_client, "InSilicoResearchAgent", payload
    )

    assert_remote_running_or_success(state, needs_artifacts=False)
