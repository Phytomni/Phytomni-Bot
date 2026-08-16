# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``AnalystAgent`` with full task polling.

Submits a rice ChIP-seq callpeak workflow on paired FASTQs plus the
NIP genome via
the stdio MCP client, and currently accepts a live remote ``RUNNING``
verdict as ``ACCEPTED_WITH_GAPS`` because callpeak is a 1h-48h job.
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


async def test_analyst_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
) -> None:
    """AnalystAgent submits and accepts remote RUNNING with gaps.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("analyst_agent.json")

    state = await submit_and_poll_to_remote_running(
        mcp_client, "AnalystAgent", payload
    )

    assert_remote_running_or_success(state, needs_artifacts=False)
