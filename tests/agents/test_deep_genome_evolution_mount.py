# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deep_genome's evolution subgraph mount.

Covers the shared finalize helper (download + sub-summary projection),
the mount factory node (input projection + degraded path), and that the
compiled deep_genome graph registers ``evolution_node``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome import evolution_mount
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)

pytestmark = pytest.mark.agent


class _StubDispatch:
    """Minimal host exposing the dispatch helpers the finalize uses."""

    def __init__(self) -> None:
        self.deep_genome_config = type("C", (), {"USER_ID": "u"})()

    @staticmethod
    def _raise_if_agent_failed(result: dict) -> None:
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError("agent failed")

    async def _download_analysis_result(
        self, _context: Any, output_path: str, _run_identity: Any
    ) -> str:
        del self
        return f"{output_path}/results"

    def _generate_sub_summary(
        self,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
    ) -> dict:
        del self, state
        return {"tree_summary": f"{analysis_type}:{gene_id}:{results_dir}"}


async def test_finalize_projects_download_and_summary() -> None:
    """The finalize helper downloads, sub-summarizes, and counts a branch."""
    host = _StubDispatch()
    task = {
        "task_id": "t1",
        "output_dir": "/obs/out",
        "task_status": "SUCCEEDED",
    }
    state: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 0,
    }
    delta = await DeepGenomeDispatchMixin.finalize_evolution_result(
        host, task=task, state=state
    )

    assert delta["analysis_completed_branches"] == 1
    assert delta["raw_analyst_data"]["task_0"]["status"] == "success"
    assert delta["raw_analyst_data"]["task_0"]["task_id"] == "t1"
    assert delta["analyst_summaries"]["tree_summary"] == (
        "evolution_analysis:g1:/obs/out/results"
    )


class _FakeApp:
    """Stand-in compiled evolution app capturing the projected input."""

    def __init__(self, output: Any = None, boom: bool = False) -> None:
        self._output = output
        self._boom = boom
        self.seen_input: Any = None

    async def ainvoke(self, payload: Any) -> Any:
        """Record the projected input and return the canned output."""
        self.seen_input = payload
        if self._boom:
            raise RuntimeError("evolution graph crashed")
        return self._output


async def test_mount_projects_input_and_finalizes() -> None:
    """The mount projects EvolutionInput and forwards (task, state)."""
    app = _FakeApp(
        output={
            "evolution_agents_task": {
                "task_id": "t1",
                "output_dir": "/obs/out",
                "task_status": "SUCCEEDED",
            }
        }
    )

    async def _finalize(task, state):
        return {
            "finalized": (
                state["species_code"],
                state["target_gene"],
                task["task_id"],
                state.get("task_index"),
            )
        }

    node = evolution_mount.make_evolution_mount_node(
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 3,
    }
    out = await node(payload)

    assert app.seen_input["target_taxids"] == "All"
    assert app.seen_input["is_polling"] is True
    assert app.seen_input["gene_id"] == "g1"
    assert out == {"finalized": ("osa", "g1", "t1", 3)}


async def test_mount_degrades_on_fault() -> None:
    """A subgraph fault yields a FailureRecord + failed branch, not a raise."""
    app = _FakeApp(boom=True)

    async def _finalize(*_args, **_kwargs):
        raise AssertionError("finalize must not run on fault")

    node = evolution_mount.make_evolution_mount_node(
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 2,
    }
    out = await node(payload)

    assert out["analysis_completed_branches"] == 1
    assert out["failures"][0]["task_label"] == "evolution_analysis"
    assert out["raw_analyst_data"]["task_2"]["status"] == "failed"
