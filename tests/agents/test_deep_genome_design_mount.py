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

pytestmark = pytest.mark.agent


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
    app = _fake_app(output={"design_task_result": [], "task_ids": {}})

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

    assert app.captured["input"]["is_polling"] is True
    assert app.captured["input"]["gene_id"] == "g1"
    assert app.captured["input"]["species_code"] == "osa"
    assert out["ok"][0] == "osa"
    assert out["ok"][1] == "g1"


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
