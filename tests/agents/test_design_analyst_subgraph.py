# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Invocation tests for the analyst-subgraph dispatch path.

Covers ``DigitalDesignAgents._dispatch_and_wait_analysis`` routing
through ``submit_analyst_via_subgraph`` and the adapter itself:
payload projection into ``AnalystInput``, ``app.ainvoke`` invocation
with the per-task ``thread_id`` ``RunnableConfig``, and the
``map_analyst_output_to_dispatch_state`` round-trip.
"""

# The direct dispatcher probe below targets the smallest design routing seam;
# its protected-access directive is scoped to that test symbol.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
    _DispatchOptions,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    map_analyst_output_to_dispatch_state,
    submit_analyst_via_subgraph,
)

from ._subgraph_branch_fakes import (
    assert_branch_taken,
    build_branch_agent,
    install_branch_mocks,
    stub_prompt_parts,
)

pytestmark = pytest.mark.agent

_DESIGN_MODULE = "mcp_server_phytomni.agents.design.agent"


@pytest.fixture(autouse=True)
def _isolate_tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the dedup tasks DB and keep output-dir creation offline.

    ``submit_analyst_via_subgraph`` now reads and writes the
    ``input_fingerprint`` dedup row; without isolation these adapter
    tests would touch the shared ``server_tasks.db`` and a prior run's
    row would trip the live-status probe (a blocked HTTP call).

    The dispatch seam also routes every fingerprinted submission through
    ``create_output_dir`` (a preset ``output_dir`` no longer
    short-circuits creation -- the fingerprint overrides it), so relay
    mode is forced on to return the shared content-addressed path without
    a real OBS round trip.
    """
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis_storage."
        "relay_mode_enabled",
        lambda: True,
    )


def _build_agent() -> DigitalDesignAgents:
    """Construct a design dispatcher for the subgraph-dispatch path."""
    return build_branch_agent(
        DigitalDesignConfig,
        DigitalDesignAgents,
        "digital_design_config",
    )


async def test_dispatch_uses_subgraph_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch always calls ``submit_analyst_via_subgraph``.

    Analyst work routes unconditionally through the compiled subgraph
    entry point; the test asserts the legacy ``submit_analyst_analysis``
    helper is never awaited (no double-dispatch, no fallback).
    """
    # pylint: disable=protected-access
    agent = _build_agent()
    legacy_mock, subgraph_mock = install_branch_mocks(
        monkeypatch, _DESIGN_MODULE
    )
    stub_prompt_parts(monkeypatch, agent)

    result = await agent._dispatch_and_wait_analysis(
        analysis_type="protein_design_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        options=_DispatchOptions(output_dir="/tmp/design-out"),
    )

    assert_branch_taken(result, legacy_mock, subgraph_mock, subgraph=True)
    # Design preserves its current fire-and-poll-elsewhere semantics:
    # must override the producer-side default of True.
    call_args = subgraph_mock.await_args
    assert call_args is not None
    assert call_args.kwargs["is_polling"] is False


async def test_via_subgraph_invokes_app_ainvoke_with_input() -> None:
    """The helper calls ``analyst_agent.app.ainvoke`` with ``AnalystInput``.

    Verifies the request payload reaches the analyst subgraph through
    the public ``ainvoke`` seam and that the projected dict carries
    every dispatch flag the subgraph entry-point expects
    (``query=""`` / ``is_preset_plan=True`` / the unpacked
    ``prompt_parts``).
    """
    ainvoke_mock = AsyncMock(
        return_value={
            "task_id": "via-subgraph-001",
            "output_dir": "/tmp/design-out",
            "plan": "step 1; step 2",
            "tool_usages": "tool_a",
            "task_status": "SUCCEEDED",
        }
    )
    analyst_stub = SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke_mock))
    config = DigitalDesignConfig()
    request = {
        "analysis_type": "protein_design_analysis",
        "target_id": "AT1G01010",
        "output_dir": "/tmp/design-out",
        "prompt_parts": (
            "Design a CRISPR knockout for AT1G01010.",
            "preset-plan-meta-blob",
            {"sample_a.tsv": "expression matrix"},
        ),
        "compute_resource": "medium",
    }

    await submit_analyst_via_subgraph(
        cast(Any, analyst_stub),
        config,
        SensitiveConfig.load(),
        request,
    )

    ainvoke_mock.assert_awaited_once()
    call_args = ainvoke_mock.await_args
    assert call_args is not None
    analyst_input = call_args.args[0]
    assert analyst_input["query"] == ""
    assert (
        analyst_input["goal_description"]
        == "Design a CRISPR knockout for AT1G01010."
    )
    assert analyst_input["preset_plan"] == "preset-plan-meta-blob"
    assert analyst_input["data_list"] == {"sample_a.tsv": "expression matrix"}
    assert analyst_input["compute_resource"] == "medium"
    assert analyst_input["is_preset_plan"] is True
    # The producer-side default is now True (forward-looking for the
    # deep_genome polling consumer). Existing dispatch consumers must
    # pass is_polling=False explicitly — that branch is exercised by
    # test_dispatch_uses_subgraph_submit_when_flag_on above.
    assert analyst_input["is_polling"] is True
    assert analyst_input["is_auto_select"] is False


async def test_via_subgraph_threads_thread_id_through_config() -> None:
    """The helper threads a per-task ``thread_id`` into ``RunnableConfig``.

    The subgraph path must give the analyst a stable ``thread_id``
    derived from the dispatch ``RunIdentity`` so checkpoint state
    keys stay consistent with the legacy ``arun`` invocation; tests
    assert the kwarg is present, non-empty, and embeds the target
    + analysis-type pair.
    """
    ainvoke_mock = AsyncMock(return_value={})
    analyst_stub = SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke_mock))
    request: dict[str, Any] = {
        "analysis_type": "promoter_design_analysis",
        "target_id": "AT2G02020",
        "output_dir": "/tmp/design-out",
        "prompt_parts": ("g", "m", {}),
        "compute_resource": "small",
    }

    await submit_analyst_via_subgraph(
        cast(Any, analyst_stub),
        DigitalDesignConfig(),
        SensitiveConfig.load(),
        request,
    )

    call_args = ainvoke_mock.await_args
    assert call_args is not None
    runnable_config = call_args.kwargs.get("config")
    assert runnable_config is not None, "ainvoke must be called with config="
    thread_id = runnable_config["configurable"]["thread_id"]
    assert isinstance(thread_id, str) and thread_id
    assert "AT2G02020" in thread_id
    assert "promoter_design_analysis" in thread_id


async def test_via_subgraph_returns_mapped_dispatch_state() -> None:
    """The helper returns exactly the dispatch-state-update key set.

    ``capture_analysis_result`` (the downstream dispatch consumer)
    reads ``task_id`` and surfaces ``output_dir`` / ``plan`` /
    ``tool_usages`` / ``task_status`` to the design parent graph.
    The expected key set is derived from
    ``map_analyst_output_to_dispatch_state({})`` so a future
    mapper-shape change propagates here without re-typing the
    literal field list.
    """
    final_state = {
        "task_id": "task-roundtrip",
        "output_dir": "/tmp/design-out",
        "plan": "executed plan",
        "tool_usages": "tool_a, tool_b",
        "task_status": "SUCCEEDED",
        "internal_scratch": "should be filtered",
    }
    analyst_stub = SimpleNamespace(
        app=SimpleNamespace(ainvoke=AsyncMock(return_value=final_state))
    )
    request: dict[str, Any] = {
        "analysis_type": "protein_design_analysis",
        "target_id": "AT1G01010",
        "output_dir": "/tmp/design-out",
        "prompt_parts": ("g", "m", {}),
        "compute_resource": "small",
    }

    result = await submit_analyst_via_subgraph(
        cast(Any, analyst_stub),
        DigitalDesignConfig(),
        SensitiveConfig.load(),
        request,
    )

    expected_keys = set(map_analyst_output_to_dispatch_state({}).keys())
    assert set(result.keys()) == expected_keys
    assert result["task_id"] == "task-roundtrip"
    assert result["task_status"] == "SUCCEEDED"
    assert "internal_scratch" not in result
