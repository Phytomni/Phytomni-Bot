# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch tests for the environment VCI analyst-subgraph routing.

Asserts ``submit_vci_task_node`` always routes through
``submit_analyst_via_subgraph`` and never falls back to the legacy
``agent.submit``. The path is pinned without constructing a real
``AnalystAgent``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.environment import graph as environment_graph
from mcp_server_phytomni.agents.environment.graph import submit_vci_task_node
from mcp_server_phytomni.agents.environment.state import EnvironmentState
from mcp_server_phytomni.agents.shared.analysis_requests import (
    AnalystAnalysisRequest,
    build_analyst_analysis_request,
)
from tests.support.environment_fakes import install_environment_submitter

pytestmark = pytest.mark.agent


def _state_with_codes() -> EnvironmentState:
    """Build an EnvironmentState ready for ``submit_vci_task_node``.

    The extract step has populated ``region_codes``; ``submit_vci_task_node``
    only reads region_codes plus the ``kwargs`` / ``batch`` overrides
    so the surrounding state stays minimal.
    """
    return {
        "query": "VCI analysis for Beijing Haidian",
        "region_codes": ["110000", "110100", "110108"],
        "kwargs": {"user_id": "user-test"},
        "batch": True,
    }


def _install_legacy_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch the prompt / data / submit dependencies for the legacy path."""

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        return {"task_id": "legacy-vci-task", "submit_kwargs": kwargs}

    submit_mock = AsyncMock(side_effect=fake_submit)
    monkeypatch.setattr(environment_graph.agent, "submit", submit_mock)
    monkeypatch.setattr(
        environment_graph.agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        environment_graph.agent,
        "get_data_list",
        lambda *_a, **_kw: ["obs://data/vci-1"],
    )
    return submit_mock


async def test_submit_vci_task_uses_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submission delegates to ``submit_analyst_via_subgraph``.

    The dispatch path forwards the goal / data / output_dir / meta into
    the dispatch request and never calls the legacy ``agent.submit``;
    the test asserts both observable conditions.
    """
    legacy_mock = _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-vci-task",
            "output_dir": "obs://run/vci-out",
            "task_status": "SUCCEEDED",
        }
    )
    install_environment_submitter(monkeypatch, subgraph_mock)

    result = await submit_vci_task_node(_state_with_codes())

    assert result["vci_analysis_task"]["task_id"] == "subgraph-vci-task"
    subgraph_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


async def test_submit_vci_subgraph_request_carries_target_and_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch packs the region codes into ``target_id`` and pins polling.

    The subgraph helper builds the dispatch request from the resolved
    province / city / county codes and threads ``is_polling=False`` so
    the analyst graph runs fire-and-poll-elsewhere just like the
    legacy ``analyst.submit`` path.
    """
    _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-vci-task"})
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    install_environment_submitter(monkeypatch, subgraph_mock)

    await submit_vci_task_node(_state_with_codes())

    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "vci_analysis"
    assert request["target_id"] == "110000-110100-110108"
    assert request["prompt_parts"][0] == "prompt-stub"
    assert (
        request["output_dir"]
        == environment_graph.ENVIRONMENT_CONFIG.OUTPUT_DIR
    )
    assert request["compute_resource"] == "large"
    assert request["prompt_parts"][1] == "prompt-stub"
    assert request["prompt_parts"][2] == ["obs://data/vci-1"]
    assert call_args.kwargs["is_polling"] is False


def test_analysis_request_builder_keeps_the_shared_payload_immutable() -> None:
    """Build one typed payload with the exact adapter-facing shape."""
    request = build_analyst_analysis_request(
        analysis_type="vci_analysis",
        target_id="110000-110100-110108",
        output_dir="obs://run/vci-out",
        prompt_parts=("goal", "meta", {"obs://data/vci-1": "vci"}),
        compute_resource="large",
    )

    assert isinstance(request, AnalystAnalysisRequest)
    assert request.to_payload() == {
        "analysis_type": "vci_analysis",
        "target_id": "110000-110100-110108",
        "output_dir": "obs://run/vci-out",
        "prompt_parts": ("goal", "meta", {"obs://data/vci-1": "vci"}),
        "compute_resource": "large",
    }
