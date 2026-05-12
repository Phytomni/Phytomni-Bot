# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""State-reduction tests for the digital design parallel-dispatch graph.

Exercises the real DigitalDesignAgents.app built by the shared
build_parallel_dispatch_graph helper to confirm that the Annotated
reducers on DigitalDesignState merge two parallel task results
correctly: operator.add for design_task_result and completed_count,
operator.or_ for task_ids.
"""

from __future__ import annotations

from typing import Any, Optional, cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


class _StubAnalyst:
    """Stand-in passed as ``analyst_agent`` to bypass real construction.

    The design agent's worker is monkeypatched in the test, so the stub
    is never actually invoked.
    """

    def identifier(self) -> str:
        """Return a stable label for debugging.

        Returns:
            Static string identifying the stub instance.
        """
        return "stub-analyst"

    def is_stub(self) -> bool:
        """Confirm this instance is a test stub, never a real agent.

        Returns:
            Always True; used by tests to assert the stub path.
        """
        return True


async def test_design_state_reduction_merges_two_parallel_tasks(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify Annotated reducers merge two parallel design tasks.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the
            real AnalystAgent dispatch with a deterministic fake.

    Returns:
        None after merged state assertions pass for design_task_result
        (operator.add), task_ids (operator.or_), and completed_count
        (operator.add).
    """
    agent = DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    dispatched: list[str] = []

    async def fake_dispatch(
        analysis_type: str,
        species: str,
        gene_id: str,
        output_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record the dispatched analysis type and return a fake result.

        Args:
            analysis_type: Design analysis type forwarded by the worker.
            species: Species forwarded by the worker.
            gene_id: Gene id forwarded by the worker.
            output_dir: Output directory forwarded by the worker.

        Returns:
            Deterministic task payload echoing ``analysis_type``.
        """
        assert species == "Arabidopsis_thaliana"
        assert gene_id == "AT1G01010"
        assert output_dir == "/tmp/design-out"
        dispatched.append(analysis_type)
        return {
            "task_id": f"task-{analysis_type}",
            "output_dir": "/tmp/design-out",
            "analysis_type": analysis_type,
        }

    monkeypatch.setattr(agent, "_dispatch_and_wait_analysis", fake_dispatch)

    initial_state = {
        "species": "Arabidopsis_thaliana",
        "gene_id": "AT1G01010",
        "user_id": "test-user",
        "batch": False,
        "output_dir": "/tmp/design-out",
        "design_task_result": [],
        "design_tasks": [],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
    }

    final_state = await agent.app.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": "design-reduction-test"}},
    )

    # prepare_tasks always emits two analyses; both should have dispatched.
    assert sorted(dispatched) == [
        "promoter_design_analysis",
        "protein_design_analysis",
    ]

    # operator.add reducer concatenated the per-task result lists.
    results = final_state["design_task_result"]
    assert len(results) == 2
    by_analysis = {r["analysis_type"]: r["task_id"] for r in results}
    assert by_analysis == {
        "protein_design_analysis": "task-protein_design_analysis",
        "promoter_design_analysis": "task-promoter_design_analysis",
    }

    # operator.or_ reducer merged the task_ids dicts; analysis_type's
    # _analysis suffix is stripped by capture_analysis_result.
    assert final_state["task_ids"] == {
        "protein_design": "task-protein_design_analysis",
        "promoter_design": "task-promoter_design_analysis",
    }

    # operator.add reducer summed two completed_count=1 increments.
    assert final_state["completed_count"] == 2
    assert final_state.get("error") is None
