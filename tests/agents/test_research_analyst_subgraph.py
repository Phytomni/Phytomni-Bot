# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch tests for InSilicoResearchAgents analyst-subgraph routing.

Asserts ``_submit_research_task`` routes through the typed
``submit_remote_analysis`` seam. Research differs from design / network:
it bypasses ``submit_analyst_analysis`` and threads a pre-computed
``thread_id`` per task; the test pins the dispatch contract without
disturbing the rest of the flow.
"""

# The direct task probes below target the smallest research dispatch seam;
# each carries a symbol-scoped protected-access directive.

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
    ResearchTaskContext,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisRequest,
)
from mcp_server_phytomni.config.models.agents import ComputeResourceName
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent(
    compute_resource: ComputeResourceName | None = None,
) -> InSilicoResearchAgents:
    """Build a research agent for analyst-subgraph dispatch tests.

    The analyst stub is a ``SimpleNamespace`` whose ``arun`` is an
    AsyncMock — dispatch is routed through a separately-patched
    ``submit_remote_analysis`` so the call is observable
    without constructing a real ``AnalystAgent``.
    """
    config = (
        InSilicoResearchConfig()
        if compute_resource is None
        else InSilicoResearchConfig(COMPUTE_RESOURCE=compute_resource)
    )
    analyst_stub = SimpleNamespace(
        arun=AsyncMock(return_value={"task_id": "legacy-task"})
    )
    return InSilicoResearchAgents(
        in_silico_config=config,
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


def _sample_task() -> ResearchTaskContext:
    """Return a frozen ResearchTaskContext for the dispatch tests."""
    return ResearchTaskContext(
        goal_description="Investigate gene X under stress.",
        context="preset-plan-meta",
        data_list={"sample_a.tsv": "expression matrix"},
        output_dir="/tmp/research-out",
        task_name="research_goal_0",
        thread_id="thread-research-goal-0",
    )


@pytest.mark.parametrize("compute_resource", [None, "medium", "large"])
async def test_submit_task_uses_subgraph(
    monkeypatch: pytest.MonkeyPatch,
    compute_resource: ComputeResourceName | None,
) -> None:
    """Dispatch delegates to ``submit_remote_analysis``.

    The path forwards the task's prompt parts and target name into
    the dispatch request and bypasses the direct ``arun`` call; the
    test asserts both observable conditions.
    """
    agent = _build_agent(compute_resource)
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-task",
            "output_dir": "/tmp/research-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.submit_remote_analysis",
        subgraph_mock,
    )

    result = await getattr(agent, "_submit_research_task")(_sample_task())

    assert result["task_id"] == "subgraph-task"
    subgraph_mock.assert_awaited_once()
    cast(AsyncMock, agent.analyst_agent.arun).assert_not_awaited()
    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert isinstance(request, RemoteAnalysisRequest)
    assert request.analysis_type == "research_goal_0"
    assert request.target_id == "research_goal_0"
    assert request.goal_description == "Investigate gene X under stress."
    assert request.meta == "preset-plan-meta"
    assert request.data_list == {"sample_a.tsv": "expression matrix"}
    assert request.output_dir == "/tmp/research-out"
    assert request.compute_resource == (compute_resource or "small")
    # Research keeps fire-and-poll-elsewhere semantics: the helper
    # is called with is_polling=False (the submit-return default).
    assert call_args.kwargs["is_polling"] is False


async def test_submit_task_propagates_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FAILED_AT_AGENT_LEVEL`` status raises ``RuntimeError``.

    The post-dispatch error check runs after the subgraph helper
    returns, so a dispatch-path failure surfaces as a
    ``RuntimeError``. Pins the contract so dispatch cannot
    accidentally swallow analyst failures.
    """
    agent = _build_agent()
    subgraph_mock = AsyncMock(
        return_value={
            "task_status": "FAILED_AT_AGENT_LEVEL",
            "error_detail": "analyst plan critic exhausted",
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.submit_remote_analysis",
        subgraph_mock,
    )

    with pytest.raises(RuntimeError, match="analyst plan critic exhausted"):
        await getattr(agent, "_submit_research_task")(_sample_task())
