# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``AnalystAgent`` with full task polling.

Submits an ATAC-seq peak-calling workflow on two rice replicates via
the stdio MCP client, extracts the task id from the submission
response, polls ``server_tasks.db`` until the task reaches a terminal
status, and asserts that the analyst output directory was published
on OBS so the regression catches both queue-only completions and
completions with no produced artifact.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.client import call_tool, submit_timeout_seconds
from .helpers.polling import extract_task_id, poll_until_done

pytestmark = pytest.mark.live


async def test_analyst_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], Dict[str, Any]],
) -> None:
    """AnalystAgent submits, polls to success, and reports an output dir.

    Args:
        mcp_client: Session-scoped MCP client.
        load_payload: Loader that returns the rewritten payload.
    """
    payload = load_payload("analyst_agent.json")

    response = await call_tool(
        mcp_client,
        "AnalystAgent",
        payload,
        timeout_seconds=submit_timeout_seconds(),
    )

    task_id = extract_task_id(response)
    state = await poll_until_done(task_id)

    assert state.succeeded, (
        f"AnalystAgent task {task_id} ended with status "
        f"{state.status!r}; expected a success terminal state."
    )
    assert state.output_dir and state.output_dir != "unupdated", (
        f"AnalystAgent task {task_id} succeeded but reported no "
        f"output directory; state={state!r}"
    )
