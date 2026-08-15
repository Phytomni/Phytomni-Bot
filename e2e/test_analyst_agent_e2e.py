# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``AnalystAgent`` with full task polling.

Submits a rice ChIP-seq callpeak workflow on paired FASTQs plus the
NIP genome via
the stdio MCP client, polls ``server_tasks.db`` until the task reaches
a terminal status, and asserts that the analyst output directory was
published on OBS so the regression catches both queue-only completions
and completions with no produced artifact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.assertions import assert_terminal_report_and_artifacts
from .helpers.polling import submit_and_poll_to_success

pytestmark = pytest.mark.live


async def test_analyst_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """AnalystAgent submits, polls to success, and reports an output dir.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("analyst_agent.json")

    state = await submit_and_poll_to_success(
        mcp_client, "AnalystAgent", payload
    )

    assert_terminal_report_and_artifacts(state, needs_artifacts=False)
