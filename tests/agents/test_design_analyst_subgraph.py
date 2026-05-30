# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch + invocation tests for the analyst-subgraph dispatch flag.

Covers ``DigitalDesignAgents._dispatch_and_wait_analysis`` branching
on ``USE_ANALYST_SUBGRAPH`` and the ``submit_analyst_via_subgraph``
adapter: payload projection into ``AnalystInput``, ``app.ainvoke``
invocation with the per-task ``thread_id`` ``RunnableConfig``, and
the ``map_analyst_output_to_dispatch_state`` round-trip.
"""

# pylint: disable=protected-access
# Test file exercises the design agent's internal helper
# (``_dispatch_and_wait_analysis``) directly to assert the flag
# branch; pylint W0212 is suppressed at file scope to mirror
# ``test_design_helpers.py`` (same protected-helper coverage seam).

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.graphs.analyst_dispatch_adapters import (
    submit_analyst_via_subgraph,
)

pytestmark = pytest.mark.agent


def _build_agent(use_subgraph: bool) -> DigitalDesignAgents:
    """Build a design agent with USE_ANALYST_SUBGRAPH set per the arg.

    Uses ``SimpleNamespace`` for the analyst stand-in (same pattern
    as ``test_design_helpers.py``) so the subgraph helper's
    ``analyst_agent.app.ainvoke`` and the legacy ``arun`` path both
    see a typed stub instead of constructing a real ``AnalystAgent``.
    """
    config = DigitalDesignConfig().model_copy(
        update={"USE_ANALYST_SUBGRAPH": use_subgraph}
    )
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return DigitalDesignAgents(
        digital_design_config=config,
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


async def test_dispatch_uses_legacy_submit_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path calls the legacy ``submit_analyst_analysis``.

    The legacy direct-``arun`` path is the production default; the
    test patches both candidate helpers on the design module's
    namespace and asserts only the legacy one was awaited.
    """
    agent = _build_agent(use_subgraph=False)
    legacy_mock = AsyncMock(return_value={"task_id": "legacy-task"})
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.design.agent.submit_analyst_analysis",
        legacy_mock,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.design.agent."
        "submit_analyst_via_subgraph",
        subgraph_mock,
    )
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_a, **_kw: ("goal", "meta", {}),
    )

    result = await agent._dispatch_and_wait_analysis(
        analysis_type="protein_design_analysis",
        species="arabidopsis thaliana",
        gene_id="AT1G01010",
        output_dir="/tmp/design-out",
    )

    assert result == {"task_id": "legacy-task"}
    legacy_mock.assert_awaited_once()
    subgraph_mock.assert_not_awaited()


async def test_dispatch_uses_subgraph_submit_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_ANALYST_SUBGRAPH=True`` calls ``submit_analyst_via_subgraph``.

    The opt-in subgraph path replaces the direct-``arun`` call with
    the compiled subgraph entry point; the test asserts the legacy
    helper is bypassed entirely so the flag's behavior is binary
    (no double-dispatch, no fallback).
    """
    agent = _build_agent(use_subgraph=True)
    legacy_mock = AsyncMock(return_value={"task_id": "legacy-task"})
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.design.agent.submit_analyst_analysis",
        legacy_mock,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.design.agent."
        "submit_analyst_via_subgraph",
        subgraph_mock,
    )
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_a, **_kw: ("goal", "meta", {}),
    )

    result = await agent._dispatch_and_wait_analysis(
        analysis_type="protein_design_analysis",
        species="arabidopsis thaliana",
        gene_id="AT1G01010",
        output_dir="/tmp/design-out",
    )

    assert result == {"task_id": "subgraph-task"}
    subgraph_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


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
    assert analyst_input["is_polling"] is False
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
    """The helper returns the five dispatch-consumed keys verbatim.

    ``capture_analysis_result`` (the downstream dispatch consumer)
    reads ``task_id`` and surfaces ``output_dir`` / ``plan`` /
    ``tool_usages`` / ``task_status`` to the design parent graph;
    the helper must project exactly these five keys so the flag-on
    path is observably indistinguishable from the legacy path at
    the dispatch-state-update boundary.
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

    assert set(result.keys()) == {
        "task_id",
        "output_dir",
        "plan",
        "tool_usages",
        "task_status",
    }
    assert result["task_id"] == "task-roundtrip"
    assert result["task_status"] == "SUCCEEDED"
    assert "internal_scratch" not in result
