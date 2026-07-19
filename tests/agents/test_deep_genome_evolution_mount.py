# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deep_genome's evolution subgraph mount.

Covers the shared finalize helper (download + sub-summary projection),
the mount factory node (input projection + degraded path), and that the
compiled deep_genome graph registers ``evolution_node``.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import evolution_mount
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)
from tests.support.subgraph_fakes import (
    assert_degraded_mount,
    mount_app,
    mount_host,
    mount_state,
)

pytestmark = pytest.mark.agent


def _stub_host() -> Any:
    """Return a host stub exposing the dispatch helpers finalize uses.

    ``mount_host`` supplies the shared coordinator callbacks while this file
    selects the evolution-specific summary key.
    """
    return mount_host(summary_key="tree_summary")


async def test_finalize_projects_download_and_summary() -> None:
    """The finalize helper downloads, sub-summarizes, and counts a branch."""
    host = _stub_host()
    task = {
        "task_id": "t1",
        "output_dir": "/obs/out",
        "task_status": "SUCCEEDED",
    }
    state: Any = mount_state("evolution", task_index=0)
    delta = await DeepGenomeDispatchMixin.finalize_evolution_result(
        host, task=task, state=state
    )

    assert delta["analysis_completed_branches"] == 1
    assert delta["raw_analyst_data"]["task_0"]["status"] == "success"
    assert delta["raw_analyst_data"]["task_0"]["task_id"] == "t1"
    assert delta["analyst_summaries"]["tree_summary"] == (
        "evolution_analysis:g1:/obs/out/results"
    )


async def test_mount_projects_input_and_finalizes() -> None:
    """The mount projects EvolutionInput and forwards (task, state)."""
    app = mount_app(
        output={
            "evolution_agents_task": {
                "task_id": "t1",
                "source_task_id": "remote-t1",
                "output_dir": "/obs/out",
            }
        }
    )

    async def _finalize(task, state):
        return {
            "finalized": (
                state["species_code"],
                state["target_gene"],
                task.submitted_task_id,
                state.get("task_index"),
            )
        }

    node = evolution_mount.make_evolution_mount_node(app.compiled, _finalize)
    payload: Any = mount_state("evolution", task_index=3)
    out = await node(payload)

    assert app.captured["input"]["target_taxids"] == "All"
    assert app.captured["input"]["is_polling"] is False
    assert app.captured["input"]["gene_id"] == "g1"
    assert out == {
        "finalized": (
            "osa",
            "g1",
            "t1",
            3,
        )
    }


async def test_mount_degrades_on_fault() -> None:
    """A subgraph fault yields a FailureRecord + failed branch, not a raise."""
    app = mount_app(error=RuntimeError("evolution graph crashed"))

    async def _finalize(*_args, **_kwargs):
        raise AssertionError("finalize must not run on fault")

    node = evolution_mount.make_evolution_mount_node(app.compiled, _finalize)
    payload: Any = mount_state("evolution", task_index=2)
    out = await node(payload)

    assert_degraded_mount(out, "evolution")


def test_deep_genome_graph_registers_evolution_node() -> None:
    """The compiled deep_genome graph exposes the evolution_node.

    Constructed offline the same way ``test_agent_smoke`` builds the
    agent (``knowledge_agent=None`` / ``analyst_agent=None``); the real
    ``_build_graph`` runs, so a missing registration fails here.
    """
    agents = DeepGenomeAgents(knowledge_agent=None, analyst_agent=None)
    nodes = set(agents.app.get_graph().nodes)
    assert "evolution_node" in nodes


async def test_mount_node_with_real_finalize_projects_summary() -> None:
    """The mount node + the real finalize compose end to end (faked app).

    Drives ``make_evolution_mount_node`` with the actual
    ``finalize_evolution_result`` (bound to a stubbed download/summary
    host) so the EvolutionInput projection and the analyst-branch delta
    are asserted as one flow.
    """
    app = mount_app(
        output={
            "evolution_agents_task": {
                "task_id": "t9",
                "output_dir": "/obs/o",
                "task_status": "SUCCEEDED",
            }
        }
    )
    host = _stub_host()

    async def _real_finalize(task, state):
        return await DeepGenomeDispatchMixin.finalize_evolution_result(
            host, task, state
        )

    node = evolution_mount.make_evolution_mount_node(
        app.compiled, _real_finalize
    )
    payload: Any = mount_state("evolution", target_gene="g9", task_index=1)
    out = await node(payload)

    assert app.captured["input"] == {
        "query": "g9",
        "species_code": "osa",
        "gene_id": "g9",
        "target_taxids": "All",
        "is_polling": False,
    }
    assert out["analysis_completed_branches"] == 1
    assert (
        out["raw_analyst_data"]["task_1:evolution_analysis"]["task_id"] == "t9"
    )
    assert out["raw_analyst_data"]["task_1:evolution_analysis"]["status"] == (
        "succeeded"
    )
    assert out["raw_analyst_data"]["task_1:evolution_analysis"][
        "poll_task_id"
    ] == ("t9")
