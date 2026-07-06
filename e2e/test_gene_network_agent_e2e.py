# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``GeneNetworkAgent`` with full task polling.

Submits the committed gene_network_agent.json payload (trait-network
analysis for rice with TO:0000207) through the stdio MCP client,
polls ``server_tasks.db`` until the task reaches terminal status, and
asserts the produced output directory is non-empty so the regression
catches network submissions that finish without writing an artifact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.polling import submit_and_poll_to_success

pytestmark = pytest.mark.live


async def test_gene_network_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """GeneNetworkAgent submits, polls to success, reports artifacts.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("gene_network_agent.json")

    state = await submit_and_poll_to_success(
        mcp_client, "GeneNetworkAgent", payload
    )

    assert state.output_dir and state.output_dir != "unupdated", (
        f"GeneNetworkAgent task {state.task_id} succeeded but "
        f"reported no output directory; state={state!r}"
    )
