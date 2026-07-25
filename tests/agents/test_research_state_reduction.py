# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""State-reduction tests for the in-silico research dispatch graph.

The tests cover both single-goal and multi-goal extraction. The latter
locks the reducer contract for ``task_ids`` and ``completed_count`` so
parallel dispatch merges remain deterministic.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
    ResearchTaskContext,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


class _StubAnalyst:
    """Stand-in passed as ``analyst_agent`` to bypass real construction.

    The research agent's worker is monkeypatched in the test, so the
    stub is never actually invoked.
    """

    def identifier(self) -> str:
        """Return a stable label for debugging.

        Returns:
            Static string identifying the stub instance.
        """
        return "stub-analyst"

    def is_stub(self) -> bool:
        """Confirm this instance is a test stub.

        Returns:
            Always True; used by tests to assert the stub path.
        """
        return True


def _build_research_agent() -> InSilicoResearchAgents:
    """Construct a research agent with a stub analyst.

    Returns:
        InSilicoResearchAgents configured with default research config
        and the in-test stub analyst.
    """
    return InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, _StubAnalyst()),
    )


async def test_research_state_reduction_dispatches_single_goal(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the single-goal dispatch path writes state slots cleanly.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the
            real LLM goal extraction and AnalystAgent dispatch with
            deterministic fakes.

    Returns:
        None after merged state assertions pass for task_ids,
        completed_count, and goals.
    """
    agent = _build_research_agent()

    extracted = [{"goal": "characterize PHYB", "context": "rice stress"}]
    submitted: list[str] = []

    async def fake_extract(
        user_query: str,
        obs_file_list: list[str],
        locale: str | None = None,
    ) -> list[dict[str, str]]:
        """Return one fixed research goal to drive single-task dispatch.

        Args:
            user_query: Paper text forwarded by extract_goals_node.
            obs_file_list: Optional OBS files (unused in the test).

        Returns:
            Fixed list with one research goal dictionary.
        """
        assert "PHYB" in user_query
        assert obs_file_list == []
        assert locale == "en-US"
        return list(extracted)

    async def fake_submit(task: ResearchTaskContext) -> dict[str, Any]:
        """Record the dispatched task name and return a fake result.

        Args:
            task: Resolved research task context forwarded by the worker.

        Returns:
            Deterministic task payload echoing ``task.task_name``.
        """
        submitted.append(task.task_name)
        return {
            "task_id": f"task-{task.task_name}",
            "output_dir": task.output_dir,
        }

    monkeypatch.setattr(agent, "_extract_goals", fake_extract)
    monkeypatch.setattr(agent, "_submit_research_task", fake_submit)

    seed_state: dict[str, Any] = {
        "error": None,
        "completed_count": 0,
        "task_ids": {},
        "research_tasks": [],
        "goals": [],
        "output_dir": "/tmp/research-out",
        "obs_file_list": [],
        "user_id": "test-user",
        "data_list": {
            "/obs/phytomni/data/rice.fa": (
                "Rice protein sequences for PHYB analysis."
            ),
        },
        "paper_text": "Study PHYB regulation in rice under drought.",
        "locale": "en-US",
    }
    config = {"configurable": {"thread_id": "research-reduction-test"}}

    final_state = await agent.app.ainvoke(seed_state, config=config)

    # extract_goals_node populated state.goals; prepare_tasks emitted
    # exactly one research task; the worker dispatched once.
    assert submitted == ["research_goal_0"]

    assert final_state["task_ids"] == {
        "research_goal_0": "task-research_goal_0",
    }
    assert final_state["completed_count"] == 1
    assert final_state["goals"] == extracted
    assert final_state.get("error") is None


async def test_research_state_reduction_handles_multiple_goals(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify two parallel research_node Sends merge through reducers.

    With ``InSilicoResearchState`` now inheriting ``ParallelDispatchState``,
    ``task_ids`` (``operator.or_``) and ``completed_count``
    (``operator.add``) carry concurrent-safe reducers, so two goals
    fan out cleanly instead of raising LangGraph's
    ``InvalidUpdateError``.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the
            real LLM goal extraction and AnalystAgent dispatch with
            deterministic fakes that emit two goals.

    Returns:
        None after both parallel branches merge their task ids and
        completion counts into the final state.
    """
    agent = _build_research_agent()

    extracted = [
        {"goal": "characterize PHYB", "context": "rice stress"},
        {"goal": "compare PHYA orthologs", "context": "evolution"},
    ]

    async def fake_extract(
        user_query: str,
        obs_file_list: list[str],
        locale: str | None = None,
    ) -> list[dict[str, str]]:
        """Return two research goals so prepare_tasks fans out twice.

        Args:
            user_query: Paper text forwarded by extract_goals_node.
            obs_file_list: Optional OBS files (unused in the test).

        Returns:
            Two-entry list of research goal dictionaries.
        """
        assert user_query
        assert obs_file_list == []
        assert locale == "en-US"
        return list(extracted)

    async def fake_submit(task: ResearchTaskContext) -> dict[str, Any]:
        """Return a fake result for whichever parallel task arrives first.

        Args:
            task: Resolved research task context forwarded by the worker.

        Returns:
            Deterministic task payload echoing ``task.task_name``.
        """
        return {
            "task_id": f"task-{task.task_name}",
            "output_dir": task.output_dir,
        }

    monkeypatch.setattr(agent, "_extract_goals", fake_extract)
    monkeypatch.setattr(agent, "_submit_research_task", fake_submit)

    final_state = await agent.app.ainvoke(
        {
            "paper_text": "Two-goal paper.",
            "data_list": {},
            "user_id": "test-user",
            "obs_file_list": [],
            "output_dir": "/tmp/research-out",
            "goals": [],
            "research_tasks": [],
            "task_ids": {},
            "completed_count": 0,
            "error": None,
            "locale": "en-US",
        },
        config={"configurable": {"thread_id": "research-multi-goal-test"}},
    )

    assert final_state["task_ids"] == {
        "research_goal_0": "task-research_goal_0",
        "research_goal_1": "task-research_goal_1",
    }
    assert final_state["completed_count"] == 2
    assert final_state["goals"] == extracted
    assert final_state.get("error") is None
