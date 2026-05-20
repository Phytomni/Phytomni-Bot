# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``DeepGenomeAgent`` with full task polling.

Submits the deep gene-function analysis for Os01g0177400 in rice
(Oryza sativa) through the stdio MCP client, polls ``server_tasks.db``
until the task
reaches terminal status, and asserts the produced output directory is
non-empty so the regression catches submissions that succeed in the
queue but never write artifacts.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.polling import submit_and_poll_to_success

pytestmark = pytest.mark.live


async def test_deep_genome_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """DeepGenomeAgent submits, polls to success, and reports artifacts.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("deep_genome_agent.json")

    state = await submit_and_poll_to_success(
        mcp_client, "DeepGenomeAgent", payload
    )

    assert state.output_dir and state.output_dir != "unupdated", (
        f"DeepGenomeAgent task {state.task_id} succeeded but reported "
        f"no output directory; state={state!r}"
    )
