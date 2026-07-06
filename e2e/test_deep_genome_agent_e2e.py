# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for ``DeepGenomeAgent`` with full task polling.

Submits the deep gene-function analysis for Os01g0177400 in rice
(Oryza sativa) through the stdio MCP client, polls ``server_tasks.db``
until the task
reaches terminal status, and asserts both a non-empty output directory
and that the persisted report carries the deep-genome title swap plus
the verbatim brief_gene preamble body, so the regression catches
submissions that succeed in the queue but never write artifacts or that
silently drop the mounted preamble.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_client_phytomni import PhytomniMcpClient

from .helpers.polling import submit_and_poll_to_success

pytestmark = pytest.mark.live


async def test_deep_genome_agent_e2e_polls_to_success(
    mcp_client: PhytomniMcpClient,
    load_payload: Callable[[str], dict[str, Any]],
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
    report = state.final_report
    assert report, (
        f"DeepGenomeAgent task {state.task_id} succeeded but persisted "
        f"no final report; state={state!r}"
    )
    assert "# Deep Genome Analysis of" in report, (
        "DeepGenomeAgent report did not swap the brief_gene preamble H1 "
        f"to the deep-genome title; report head: {report[:200]!r}"
    )
    assert "## Gene Profiles" in report, (
        "DeepGenomeAgent report lacked the verbatim brief_gene "
        f"'## Gene Profiles' preamble body; report head: {report[:200]!r}"
    )
    assert "## Bioinformatic Analysis" in report, (
        "DeepGenomeAgent report lacked the '## Bioinformatic Analysis' "
        f"body appended after the preamble; report head: {report[:200]!r}"
    )
