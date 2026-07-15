# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deep_genome's digital-design subgraph mount.

Covers the mount factory node (input projection + degraded path) and,
later, the shared finalize helper that lights the §8.2 protein-design
section from the mounted graph's protein-design task.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome import design_mount
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)

pytestmark = pytest.mark.agent


def _stub_host() -> Any:
    """Return a host stub exposing the dispatch helpers finalize uses.

    A ``SimpleNamespace`` rather than a class keeps the test free of a
    single-public-method stand-in (the canonical R0903 source), matching
    how the evolution-mount test builds its mixin host. ``downloaded``
    records the output dirs the finalize actually fetched.
    """
    downloaded: list[str] = []

    def _raise_if_agent_failed(result: dict) -> None:
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError("agent failed")

    async def _download_analysis_result(
        _context: Any, output_path: str, _run_identity: Any
    ) -> str:
        downloaded.append(output_path)
        return f"{output_path}/results"

    def _generate_sub_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
    ) -> dict:
        del state
        return {"protein_summary": f"{analysis_type}:{gene_id}:{results_dir}"}

    return SimpleNamespace(
        deep_genome_config=SimpleNamespace(USER_ID="u"),
        _raise_if_agent_failed=_raise_if_agent_failed,
        _download_analysis_result=_download_analysis_result,
        _generate_sub_summary=_generate_sub_summary,
        downloaded=downloaded,
    )


async def test_finalize_summarizes_protein_design_only() -> None:
    """Finalize downloads + summarizes only the protein-design task.

    The mounted graph returns both design tasks; §8.1 promoter rendering
    is deferred, so finalize correlates the ``protein_design`` task id,
    downloads only it, and contributes the single barrier branch.
    """
    host = _stub_host()
    design_output = {
        "task_ids": {"protein_design": "p1", "promoter_design": "m1"},
        "design_task_result": [
            {
                "task_id": "p1",
                "output_dir": "/obs/p",
                "task_status": "SUCCEEDED",
            },
            {
                "task_id": "m1",
                "output_dir": "/obs/m",
                "task_status": "SUCCEEDED",
            },
        ],
    }
    state: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 6,
    }

    delta = await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    assert delta["analysis_completed_branches"] == 1
    assert delta["raw_analyst_data"]["task_6"]["task_id"] == "p1"
    assert delta["raw_analyst_data"]["task_6"]["analysis_type"] == (
        "digital_design"
    )
    assert delta["analyst_summaries"]["protein_summary"].startswith(
        "protein_design_analysis:g1:"
    )
    # Only the protein-design task is downloaded; promoter is deferred.
    assert host.downloaded == ["/obs/p"]


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
            raise RuntimeError("design graph crashed")
        return output

    return SimpleNamespace(ainvoke=ainvoke, captured=captured)


async def test_mount_projects_input_and_finalizes() -> None:
    """The mount projects DigitalDesignState input and forwards output."""
    app = _fake_app(
        output={
            "design_task_result": [
                {
                    "analysis_type": "protein_design_analysis",
                    "task_id": "",
                    "output_dir": "/obs/invalid",
                },
                {
                    "analysis_type": "protein_design_analysis",
                    "task_id": "protein-caller",
                    "source_task_id": "protein-remote",
                    "output_dir": "/obs/protein",
                },
                {
                    "analysis_type": "promoter_design_analysis",
                    "task_id": "promoter-caller",
                    "output_dir": "/obs/promoter",
                },
            ],
            "task_ids": {
                "protein_design": "protein-caller",
                "promoter_design": "promoter-caller",
            },
        }
    )

    async def _finalize(design_output, state):
        return {
            "ok": (
                state["species_code"],
                state["target_gene"],
                design_output,
            )
        }

    node = design_mount.make_design_mount_node(
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 5,
    }
    out = await node(payload)

    assert app.captured["input"]["is_polling"] is False
    assert app.captured["input"]["gene_id"] == "g1"
    assert app.captured["input"]["species_code"] == "osa"
    assert out["ok"][0] == "osa"
    assert out["ok"][1] == "g1"
    assert out["ok"][2] == {
        "protein_design": RemoteSubmission(
            submitted_task_id="protein-caller",
            poll_task_id="protein-remote",
            output_dir="/obs/protein",
        ),
        "promoter_design": RemoteSubmission(
            submitted_task_id="promoter-caller",
            poll_task_id="promoter-caller",
            output_dir="/obs/promoter",
        ),
    }


async def test_mount_keeps_one_design_submission_when_sibling_fails() -> None:
    """A producer failure does not discard its independent sibling."""
    app = _fake_app(
        output={
            "design_task_result": [
                {
                    "analysis_type": "protein_design_analysis",
                    "task_id": "protein-caller",
                    "output_dir": "/obs/protein",
                }
            ],
            "task_ids": {"protein_design": "protein-caller"},
            "failures": [
                {"task_label": "promoter_design_analysis", "message": "x"}
            ],
        }
    )

    async def _capture(submissions, _state):
        return {"submissions": submissions}

    node = design_mount.make_design_mount_node(
        cast(CompiledStateGraph, app), _capture
    )
    out = await node(
        {"species_code": "osa", "target_gene": "g1", "task_index": 1}
    )

    assert out["submissions"]["protein_design"] is not None
    assert out["submissions"]["promoter_design"] is None


async def test_mount_keeps_valid_design_with_malformed_sibling() -> None:
    """Malformed optional entries do not discard a valid sibling."""
    app = _fake_app(
        output={
            "design_task_result": [
                {
                    "analysis_type": "protein_design_analysis",
                    "task_id": "protein-caller",
                    "output_dir": "/obs/protein",
                },
                {},
                "not-an-object",
            ],
            "task_ids": {"protein_design": "protein-caller"},
        }
    )

    async def _capture(submissions, _state):
        return {"submissions": submissions}

    node = design_mount.make_design_mount_node(
        cast(CompiledStateGraph, app), _capture
    )
    out = await node(
        {"species_code": "osa", "target_gene": "g1", "task_index": 1}
    )

    assert out["submissions"]["protein_design"] is not None
    assert out["submissions"]["promoter_design"] is None


def test_route_carries_concrete_work_item_order_into_send_payload() -> None:
    """Send payloads preserve the stable work-item display order."""
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": "single_cell_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            }
        ],
        "work_items": [
            {
                "section_key": "single_cell_analysis",
                "work_item_key": "single_cell_analysis",
                "display_order": 4,
            }
        ],
    }
    route = getattr(DeepGenomeDispatchMixin, "_route_analyst_tasks")
    sends = route(object(), state)

    assert sends[0].arg["work_item_key"] == "single_cell_analysis"
    assert sends[0].arg["display_order"] == 4


def test_deep_genome_graph_registers_design_node() -> None:
    """The compiled deep_genome graph exposes the design_node.

    Constructed offline the same way ``test_agent_smoke`` builds the
    agent (``knowledge_agent=None`` / ``analyst_agent=None``); the real
    ``_build_graph`` runs, so a missing registration fails here.
    """
    agents = DeepGenomeAgents(knowledge_agent=None, analyst_agent=None)
    nodes = set(agents.app.get_graph().nodes)
    assert "design_node" in nodes


def test_deep_genome_graph_registers_nine_generic_nodes() -> None:
    """The compiled graph exposes the nine per-type generic nodes.

    SP3 split the single ``analyst_node`` into one named node per generic
    analysis_type; the old ``analyst_node`` is gone.
    """
    agents = DeepGenomeAgents(knowledge_agent=None, analyst_agent=None)
    nodes = set(agents.app.get_graph().nodes)
    expected = {
        "gene_expression_tissues_node",
        "gene_expression_cultivars_node",
        "gene_expression_treatments_node",
        "gene_expression_genotypes_node",
        "single_cell_node",
        "promoter_node",
        "smep_node",
        "smoc_node",
        "protein_structure_node",
    }
    assert expected <= nodes
    assert "analyst_node" not in nodes


async def test_mount_degrades_on_fault() -> None:
    """A subgraph fault yields a FailureRecord + failed branch, not a raise."""
    app = _fake_app(boom=True)

    async def _finalize(*_args, **_kwargs):
        raise AssertionError("finalize must not run on fault")

    node = design_mount.make_design_mount_node(
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 2,
    }
    out = await node(payload)

    assert out["analysis_completed_branches"] == 1
    assert out["failures"][0]["task_label"] == "digital_design"
    assert out["raw_analyst_data"]["task_2"]["status"] == "failed"
