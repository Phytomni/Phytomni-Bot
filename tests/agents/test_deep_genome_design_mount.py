# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deep_genome's digital-design subgraph mount.

Covers the mount factory node (input projection + degraded path) and,
later, the shared finalize helper that lights the §8.2 protein-design
section from the mounted graph's protein-design task.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import design_mount
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
)
from tests.support.subgraph_fakes import (
    DEEP_GENOME_GENERIC_NODE_NAMES,
    assert_degraded_mount,
    mount_app,
    mount_host,
    mount_state,
)

pytestmark = pytest.mark.agent


def _stub_host() -> Any:
    """Return a host stub exposing the dispatch helpers finalize uses.

    ``mount_host`` supplies the shared coordinator callbacks and records the
    output dirs the finalize actually fetched. The design-only callbacks stay
    local because they bind the mixin's design work-item implementation.
    """
    host = mount_host(summary_key="protein_summary")
    setattr(
        host,
        "_poll_design_work_item",
        partial(
            getattr(DeepGenomeDispatchMixin, "_poll_design_work_item"), host
        ),
    )
    setattr(
        host,
        "_finalize_design_submissions",
        partial(
            getattr(DeepGenomeDispatchMixin, "_finalize_design_submissions"),
            host,
        ),
    )
    return host


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
    state: Any = mount_state("design", task_index=6)

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


async def test_finalize_polls_both_independent_design_work_items() -> None:
    """Poll protein and promoter submissions independently before success."""
    host = _stub_host()
    summary_orders: dict[str, Any] = {}

    def _capture_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
        display_order_override: int | None = None,
    ) -> dict:
        del state
        summary_orders[analysis_type] = display_order_override
        return {f"{analysis_type}_summary": f"{gene_id}:{results_dir}"}

    setattr(host, "_generate_sub_summary", _capture_summary)
    design_output = {
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
    state: Any = mount_state(
        "design",
        task_index=4,
        work_items=[
            {"work_item_key": "protein_design", "display_order": 10},
            {"work_item_key": "promoter_design", "display_order": 11},
        ],
    )

    delta = await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    assert delta["analysis_completed_branches"] == 1
    assert delta["raw_analyst_data"]["task_4:protein_design"]["status"] == (
        "succeeded"
    )
    assert delta["raw_analyst_data"]["task_4:promoter_design"]["status"] == (
        "succeeded"
    )
    assert summary_orders == {
        "protein_design_analysis": 10,
        "promoter_analysis": 11,
    }
    assert set(host.downloaded) == {"/obs/promoter", "/obs/protein"}


async def test_finalize_polls_design_work_items_concurrently() -> None:
    """Do not wait for promoter before starting the protein poll clock."""
    host = _stub_host()
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()
    both_started = asyncio.Event()

    async def _poll_remote_submission(
        submission: RemoteSubmission,
        _context: Any,
        _run_identity: Any,
        **_kwargs: Any,
    ) -> tuple[WorkItemOutcome, str]:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if in_flight >= 2:
                both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        async with lock:
            in_flight -= 1
        return (
            WorkItemOutcome("succeeded", "# usable result", None),
            f"{submission.output_dir}/results",
        )

    setattr(host, "_poll_remote_submission", _poll_remote_submission)
    design_output = {
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
    state: Any = mount_state("design", task_index=4)

    delta = await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    assert max_in_flight == 2
    protein = delta["raw_analyst_data"]["task_4:protein_design"]
    promoter = delta["raw_analyst_data"]["task_4:promoter_design"]
    assert protein["status"] == "succeeded"
    assert promoter["status"] == "succeeded"


async def test_finalize_gives_protein_design_a_longer_poll_budget() -> None:
    """Protein design keeps a 48h local clock independent of MAX_POLL."""
    host = _stub_host()
    seen: dict[str, float | None] = {}
    setattr(host.deep_genome_config, "MAX_POLL", 10.0)
    setattr(host.deep_genome_config, "PROTEIN_DESIGN_MAX_POLL", 172800.0)

    async def _poll_remote_submission(
        submission: RemoteSubmission,
        _context: Any,
        _run_identity: Any,
        **kwargs: Any,
    ) -> tuple[WorkItemOutcome, str]:
        seen[submission.poll_task_id] = kwargs.get("deadline_seconds")
        return (
            WorkItemOutcome("succeeded", "# usable result", None),
            f"{submission.output_dir}/results",
        )

    setattr(host, "_poll_remote_submission", _poll_remote_submission)
    design_output = {
        "protein_design": RemoteSubmission(
            submitted_task_id="protein-caller",
            poll_task_id="protein-remote",
            output_dir="/obs/protein",
        ),
        "promoter_design": RemoteSubmission(
            submitted_task_id="promoter-caller",
            poll_task_id="promoter-remote",
            output_dir="/obs/promoter",
        ),
    }
    state: Any = mount_state("design")

    await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    assert seen["protein-remote"] == 172800.0
    assert seen["promoter-remote"] is None


async def test_finalize_keeps_partial_design_failure_and_blank_result() -> (
    None
):
    """Keep a usable sibling when the other result is blank/unusable."""
    host = _stub_host()

    async def _poll_remote_submission(
        submission: RemoteSubmission,
        _context: Any,
        _run_identity: Any,
        **_kwargs: Any,
    ) -> tuple[WorkItemOutcome, str | None]:
        if submission.poll_task_id == "promoter-remote":
            return (
                WorkItemOutcome("failed", None, "analysis result unusable"),
                None,
            )
        return (
            WorkItemOutcome("succeeded", "# protein result", None),
            "/obs/protein/results",
        )

    setattr(host, "_poll_remote_submission", _poll_remote_submission)
    design_output = {
        "protein_design": RemoteSubmission(
            submitted_task_id="protein-caller",
            poll_task_id="protein-remote",
            output_dir="/obs/protein",
        ),
        "promoter_design": RemoteSubmission(
            submitted_task_id="promoter-caller",
            poll_task_id="promoter-remote",
            output_dir="/obs/promoter",
        ),
    }
    state: Any = mount_state("design", task_index=4)

    delta = await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    protein = delta["raw_analyst_data"]["task_4:protein_design"]
    promoter = delta["raw_analyst_data"]["task_4:promoter_design"]
    assert protein["status"] == "succeeded"
    assert promoter["status"] == "failed"
    assert promoter["error"] == "analysis result unusable"
    assert "protein_summary" in delta["analyst_summaries"]
    assert "promoter_summary" not in delta["analyst_summaries"]


async def test_finalize_isolates_design_summary_failure() -> None:
    """A summary error fails one item while preserving its sibling."""
    host = _stub_host()

    def _fail_promoter_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
        display_order_override: int | None = None,
    ) -> dict:
        del gene_id, state, results_dir, display_order_override
        if analysis_type == "promoter_analysis":
            raise UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid")
        return {"protein_summary": "usable"}

    setattr(host, "_generate_sub_summary", _fail_promoter_summary)
    design_output = {
        "protein_design": RemoteSubmission(
            submitted_task_id="protein-caller",
            poll_task_id="protein-remote",
            output_dir="/obs/protein",
        ),
        "promoter_design": RemoteSubmission(
            submitted_task_id="promoter-caller",
            poll_task_id="promoter-remote",
            output_dir="/obs/promoter",
        ),
    }
    state: Any = mount_state("design", task_index=4)

    delta = await DeepGenomeDispatchMixin.finalize_design_result(
        host, design_output=design_output, state=state
    )

    assert delta["raw_analyst_data"]["task_4:protein_design"]["status"] == (
        "succeeded"
    )
    promoter = delta["raw_analyst_data"]["task_4:promoter_design"]
    assert promoter["status"] == "failed"
    assert promoter["error"] == "analysis summary generation failed"
    assert delta["analyst_summaries"] == {"protein_summary": "usable"}


async def test_mount_projects_input_and_finalizes() -> None:
    """The mount projects DigitalDesignState input and forwards output."""
    app = mount_app(
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

    node = design_mount.make_design_mount_node(app.compiled, _finalize)
    payload: Any = mount_state("design", task_index=5)
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
    app = mount_app(
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

    node = design_mount.make_design_mount_node(app.compiled, _capture)
    out = await node(mount_state("design", task_index=1))

    assert out["submissions"]["protein_design"] is not None
    assert out["submissions"]["promoter_design"] is None


async def test_mount_keeps_valid_design_with_malformed_sibling() -> None:
    """Malformed optional entries do not discard a valid sibling."""
    app = mount_app(
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

    node = design_mount.make_design_mount_node(app.compiled, _capture)
    out = await node(mount_state("design", task_index=1))

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
    expected = set(DEEP_GENOME_GENERIC_NODE_NAMES)
    assert expected <= nodes
    assert "analyst_node" not in nodes


async def test_mount_degrades_on_fault() -> None:
    """A subgraph fault yields a FailureRecord + failed branch, not a raise."""
    app = mount_app(error=RuntimeError("design graph crashed"))

    async def _finalize(*_args, **_kwargs):
        raise AssertionError("finalize must not run on fault")

    node = design_mount.make_design_mount_node(app.compiled, _finalize)
    payload: Any = mount_state("design", task_index=2)
    out = await node(payload)

    assert_degraded_mount(out, "design")
