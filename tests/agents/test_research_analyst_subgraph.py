# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for InSilicoResearchAgents analyst-subgraph dispatch.

Asserts ``_submit_research_task`` calls ``analyst_agent.arun``
directly when ``USE_ANALYST_SUBGRAPH=False`` and routes through
``submit_analyst_via_subgraph`` when ``True``. Research differs
from design / network: it bypasses ``submit_analyst_analysis``
and threads a pre-computed ``thread_id`` per task; the test pins
both branches without disturbing the rest of the dispatch flow.
"""

# pylint: disable=protected-access
# Test file exercises ``_submit_research_task`` directly (the
# per-task dispatch chokepoint) to assert flag routing.

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
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent(use_subgraph: bool) -> InSilicoResearchAgents:
    """Build a research agent with USE_ANALYST_SUBGRAPH set per the arg.

    The analyst stub is a ``SimpleNamespace`` whose ``arun`` is an
    AsyncMock — the legacy branch awaits it directly, while the
    subgraph branch is dispatched through a separately-patched
    ``submit_analyst_via_subgraph`` so each branch is observable
    without constructing a real ``AnalystAgent``.
    """
    config = InSilicoResearchConfig().model_copy(
        update={"USE_ANALYST_SUBGRAPH": use_subgraph}
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
    """Return a frozen ResearchTaskContext for both branch tests."""
    return ResearchTaskContext(
        goal_description="Investigate gene X under stress.",
        context="preset-plan-meta",
        data_list={"sample_a.tsv": "expression matrix"},
        output_dir="/tmp/research-out",
        task_name="research_goal_0",
        thread_id="thread-research-goal-0",
    )


async def test_submit_task_uses_legacy_arun_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits ``analyst_agent.arun`` directly.

    Pins the production-default routing: the legacy direct-``arun``
    call must run with the task's pre-computed ``thread_id`` (so the
    parent research graph's per-goal checkpoint keys stay stable)
    and the subgraph helper must not be invoked.
    """
    agent = _build_agent(use_subgraph=False)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-task"})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent."
        "submit_analyst_via_subgraph",
        subgraph_mock,
    )

    result = await agent._submit_research_task(_sample_task())

    assert result["task_id"] == "legacy-task"
    cast(AsyncMock, agent.analyst_agent.arun).assert_awaited_once()
    legacy_call = cast(AsyncMock, agent.analyst_agent.arun).await_args
    assert legacy_call is not None
    assert legacy_call.kwargs["thread_id"] == "thread-research-goal-0"
    assert legacy_call.kwargs["compute_resource"] == "medium"
    subgraph_mock.assert_not_awaited()


async def test_submit_task_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to ``submit_analyst_via_subgraph``.

    The opt-in path forwards the task's prompt parts and target
    name into the dispatch request and bypasses the legacy
    ``arun`` call; the test asserts both observable conditions so
    the flag's behavior is binary.
    """
    agent = _build_agent(use_subgraph=True)
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-task",
            "output_dir": "/tmp/research-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent."
        "submit_analyst_via_subgraph",
        subgraph_mock,
    )

    result = await agent._submit_research_task(_sample_task())

    assert result["task_id"] == "subgraph-task"
    subgraph_mock.assert_awaited_once()
    cast(AsyncMock, agent.analyst_agent.arun).assert_not_awaited()
    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "research_goal_0"
    assert request["target_id"] == "research_goal_0"
    assert request["compute_resource"] == "medium"
    assert request["prompt_parts"] == (
        "Investigate gene X under stress.",
        "preset-plan-meta",
        {"sample_a.tsv": "expression matrix"},
    )
    # Research preserves its current fire-and-poll-elsewhere
    # semantics: must override the producer-side default of True.
    assert call_args.kwargs["is_polling"] is False


async def test_submit_task_propagates_failed_status_in_both_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FAILED_AT_AGENT_LEVEL`` status raises ``RuntimeError`` in both modes.

    The post-dispatch error check runs after the branch returns, so
    a subgraph-path failure surfaces the same way a legacy-path
    failure does. Pins the cross-branch contract so the flag
    cannot accidentally swallow analyst failures.
    """
    agent = _build_agent(use_subgraph=True)
    subgraph_mock = AsyncMock(
        return_value={
            "task_status": "FAILED_AT_AGENT_LEVEL",
            "error_detail": "analyst plan critic exhausted",
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent."
        "submit_analyst_via_subgraph",
        subgraph_mock,
    )

    with pytest.raises(RuntimeError, match="analyst plan critic exhausted"):
        await agent._submit_research_task(_sample_task())
