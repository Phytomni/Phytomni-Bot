# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""State-reduction tests for the gene network parallel-dispatch graph.

Network's prepare_tasks emits exactly one task, so its state slots
need no Annotated reducers. The test exercises the real compiled
graph and asserts the single-task dispatch path writes network_task,
task_ids, and completed_count cleanly after the builder migration.
"""

from __future__ import annotations

from typing import Any, Optional, cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.network.agent import (
    GeneNetworkAgents,
    GeneNetworkConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


class _StubAnalyst:
    """Stand-in passed as ``analyst_agent`` to bypass real construction.

    The network agent's worker is monkeypatched in the test, so the
    stub is never actually invoked.
    """

    def identifier(self) -> str:
        """Return a stable label for debugging.

        Returns:
            Static string identifying the stub instance.
        """
        return "stub-analyst"

    def is_stub(self) -> bool:
        """Confirm this instance is a test stub.

        Returns:
            Always True; used by tests to assert the stub path.
        """
        return True


async def test_network_state_reduction_dispatches_single_task(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify single-task dispatch writes all state slots cleanly.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the
            real AnalystAgent dispatch with a deterministic fake.

    Returns:
        None after merged state assertions pass for network_task,
        task_ids, and completed_count.
    """
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    dispatched: list[str] = []

    async def fake_dispatch(
        analysis_type: str,
        species: str,
        to_id: str,
        output_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record the dispatched analysis type and return a fake result.

        Args:
            analysis_type: Network analysis type forwarded by the worker.
            species: Species forwarded by the worker.
            to_id: Target id forwarded by the worker.
            output_dir: Output directory forwarded by the worker.

        Returns:
            Deterministic task payload echoing ``analysis_type``.
        """
        assert species == "rice"
        assert to_id == "Os01g01010"
        assert output_dir == "/tmp/network-out"
        dispatched.append(analysis_type)
        return {
            "task_id": f"task-{analysis_type}",
            "output_dir": "/tmp/network-out",
            "analysis_type": analysis_type,
        }

    monkeypatch.setattr(agent, "_dispatch_and_wait_analysis", fake_dispatch)

    initial_state = {
        "species": "rice",
        "to_id": "Os01g01010",
        "user_id": "test-user",
        "batch": False,
        "output_dir": "/tmp/network-out",
        "network_task": {},
        "network_tasks": [],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
    }

    final_state = await agent.app.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": "network-reduction-test"}},
    )

    # prepare_tasks emits exactly one analysis ("gene_network_analysis"),
    # so a single Send fans out to the worker and writes the slots once.
    assert dispatched == ["gene_network_analysis"]

    network_task = final_state["network_task"]
    assert network_task["task_id"] == "task-gene_network_analysis"
    assert network_task["analysis_type"] == "gene_network_analysis"

    # task_ids strips the _analysis suffix from the dispatched analysis
    # type so the network key is stored without the suffix.
    assert final_state["task_ids"] == {
        "gene_network": "task-gene_network_analysis",
    }

    assert final_state["completed_count"] == 1
    assert final_state.get("error") is None
