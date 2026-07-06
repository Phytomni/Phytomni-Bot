# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""State-reduction tests for the gene network parallel-dispatch graph.

The default prepare_tasks emits a single task, but task_ids,
completed_count, and error now carry Annotated reducers so a
multi-task fan-out merges concurrent worker updates instead of
raising LangGraph's InvalidUpdateError. The single-task test covers
the happy path; the dual-failure test pins the reducer fix.
"""

from __future__ import annotations

from typing import Any, cast

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
        species_code: str,
        to_id: str,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Record the dispatched analysis type and return a fake result.

        Args:
            analysis_type: Network analysis type forwarded by the worker.
            species_code: Species code forwarded by the worker.
            to_id: Target id forwarded by the worker.
            output_dir: Output directory forwarded by the worker.

        Returns:
            Deterministic task payload echoing ``analysis_type``.
        """
        assert species_code == "osa"
        assert to_id == "TO:0000207"
        assert output_dir == "/tmp/network-out"
        dispatched.append(analysis_type)
        return {
            "task_id": f"task-{analysis_type}",
            "output_dir": "/tmp/network-out",
            "analysis_type": analysis_type,
        }

    monkeypatch.setattr(agent, "_dispatch_and_wait_analysis", fake_dispatch)

    initial_state: dict[str, Any] = dict.fromkeys(["error"], None)
    initial_state.update(
        species_code="osa",
        to_id="TO:0000207",
        user_id="test-user",
        batch=False,
        output_dir="/tmp/network-out",
        network_task={},
        network_tasks=[],
        task_ids={},
        completed_count=0,
    )

    merged = await agent.app.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": "network-reduction-test"}},
    )

    # prepare_tasks emits exactly one analysis ("gene_network_analysis"),
    # so a single Send fans out to the worker and writes the slots once.
    assert dispatched == ["gene_network_analysis"]

    network_task = merged["network_task"]
    assert network_task["task_id"] == "task-gene_network_analysis"
    assert network_task["analysis_type"] == "gene_network_analysis"

    # task_ids strips the _analysis suffix from the dispatched analysis
    # type so the network key is stored without the suffix.
    assert merged["task_ids"] == {
        "gene_network": "task-gene_network_analysis",
    }

    assert merged["completed_count"] == 1
    assert merged.get("error") is None


async def test_network_state_reduction_handles_dual_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify two concurrently failing network tasks merge without error.

    The default prepare_tasks emits one task, so the latent reducer gap
    never surfaced in production; here prepare_tasks is overridden to
    fan out two tasks that both fail, so each Send branch concurrently
    writes task_ids, completed_count, and error. Without the Annotated
    reducers LangGraph raises InvalidUpdateError on the merge.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to force a two-task
            fan-out where both dispatches fail deterministically.

    Returns:
        None after the merged state proves the reducers worked.
    """

    async def fake_prepare_tasks(
        self: GeneNetworkAgents,
        state: Any,
    ) -> dict[str, Any]:
        """Fan out two network tasks instead of the default one.

        Args:
            self: Bound GeneNetworkAgents instance (unused).
            state: Current workflow state (unused).

        Returns:
            State update seeding two parallel network tasks.
        """
        _ = (self, state)
        return {
            "network_tasks": [
                {"analysis_type": "gene_network_analysis"},
                {"analysis_type": "gene_network_analysis"},
            ],
            "task_ids": {},
            "completed_count": 0,
        }

    # Patch the class before construction: _build_graph binds the
    # prepare node into the compiled graph inside __init__, so an
    # instance patch applied afterwards would never take effect.
    monkeypatch.setattr(GeneNetworkAgents, "prepare_tasks", fake_prepare_tasks)
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )

    async def fake_dispatch(
        analysis_type: str,
        species_code: str,
        to_id: str,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Fail every dispatched network task deterministically.

        Args:
            analysis_type: Network analysis label for the failing task.
            species_code: Species code forwarded by the dispatcher.
            to_id: Trait Ontology id forwarded by the dispatcher.
            output_dir: Optional output directory (unused).

        Raises:
            RuntimeError: Always, tagged with the analysis type.
        """
        assert species_code == "osa"
        assert to_id == "TO:0000207"
        _ = output_dir
        raise RuntimeError(f"boom {analysis_type}")

    monkeypatch.setattr(agent, "_dispatch_and_wait_analysis", fake_dispatch)

    seed_state: dict[str, Any] = dict.fromkeys(["error"], None)
    seed_state.update(
        species_code="osa",
        to_id="TO:0000207",
        user_id="test-user",
        batch=False,
        output_dir="/tmp/network-out",
        network_task={},
        network_tasks=[],
        task_ids={},
        completed_count=0,
    )

    final_state = await agent.app.ainvoke(
        seed_state,
        config={"configurable": {"thread_id": "network-dual-failure-test"}},
    )

    # Both branches failed and merged cleanly: completed_count summed via
    # operator.add, error retained by keep_last_error, task_ids or_-merged.
    assert final_state["completed_count"] == 2
    assert isinstance(final_state.get("error"), str)
    assert final_state["error"].startswith("boom ")
    assert final_state["task_ids"] == {}
    # failures accumulates both records via operator.add reducer.
    assert "failures" in final_state
    assert len(final_state["failures"]) == 2
    assert all(
        f["message"].startswith("boom") for f in final_state["failures"]
    )
    assert all(f["kind"] == "execute" for f in final_state["failures"])
    assert all(
        "task_label" in f and "traceback_digest" in f
        for f in final_state["failures"]
    )
