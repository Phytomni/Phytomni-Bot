# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deep_genome's evolution subgraph mount.

Covers the shared finalize helper (download + sub-summary projection),
the mount factory node (input projection + degraded path), and that the
compiled deep_genome graph registers ``evolution_node``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome import evolution_mount
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)

pytestmark = pytest.mark.agent


def _stub_host() -> Any:
    """Return a host stub exposing the dispatch helpers finalize uses.

    A ``SimpleNamespace`` rather than a class keeps the test free of a
    single-public-method stand-in (the canonical R0903 source), matching
    how ``test_deep_genome_dispatch_routing`` builds its mixin host.
    """

    def _raise_if_agent_failed(result: dict) -> None:
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError("agent failed")

    async def _download_analysis_result(
        _context: Any, output_path: str, _run_identity: Any
    ) -> str:
        return f"{output_path}/results"

    async def _poll_remote_submission(
        submission: RemoteSubmission,
        context: Any,
        run_identity: Any,
        **_kwargs: Any,
    ) -> tuple[WorkItemOutcome, str]:
        results_dir = await _download_analysis_result(
            context,
            submission.output_dir,
            run_identity,
        )
        return (
            WorkItemOutcome("succeeded", "# usable result", None),
            results_dir,
        )

    def _generate_sub_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
    ) -> dict:
        del state
        return {"tree_summary": f"{analysis_type}:{gene_id}:{results_dir}"}

    return SimpleNamespace(
        deep_genome_config=SimpleNamespace(USER_ID="u"),
        _raise_if_agent_failed=_raise_if_agent_failed,
        _download_analysis_result=_download_analysis_result,
        _poll_remote_submission=_poll_remote_submission,
        _generate_sub_summary=_generate_sub_summary,
    )


async def test_finalize_projects_download_and_summary() -> None:
    """The finalize helper downloads, sub-summarizes, and counts a branch."""
    host = _stub_host()
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


def _fake_app(output: Any = None, boom: bool = False) -> Any:
    """Return a fake compiled app capturing the projected input.

    A ``SimpleNamespace`` with an ``ainvoke`` closure (rather than a
    one-method class) avoids the R0903 too-few-public-methods report;
    the captured payload is read back via ``app.captured['input']``.
    """
    captured: dict = {}

    async def ainvoke(payload: Any) -> Any:
        captured["input"] = payload
        if boom:
            raise RuntimeError("evolution graph crashed")
        return output

    return SimpleNamespace(ainvoke=ainvoke, captured=captured)


async def test_mount_projects_input_and_finalizes() -> None:
    """The mount projects EvolutionInput and forwards (task, state)."""
    app = _fake_app(
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

    node = evolution_mount.make_evolution_mount_node(
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 3,
    }
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
    app = _fake_app(boom=True)

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
    app = _fake_app(
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
        cast(CompiledStateGraph, app), _real_finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g9",
        "task_index": 1,
    }
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
