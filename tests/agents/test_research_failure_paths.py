# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Failure-path tests for the in-silico research agent's node handlers.

Pin two branches the happy-path state-reduction test skips: the
``extract_goals_node`` failure_state callback (writes ``goals=[]``
and the stringified error into state when ``_extract_goals``
raises), and the ``run_research_node`` ``ValueError`` guard against
a state carrying ``output_dir=None``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import mcp_server_phytomni.agents.research.agent as research_agent_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.research.agent import (
    InSilicoResearchAgents,
    InSilicoResearchConfig,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisSubmissionError,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> InSilicoResearchAgents:
    """Construct a research agent wired to a no-op analyst stub.

    Uses ``SimpleNamespace`` for the analyst stand-in (same pattern as
    ``_analyst_fakes.py``) so pylint's R0903 too-few-public-methods
    rule does not trip on a single-method stub class.
    """
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return InSilicoResearchAgents(
        in_silico_config=InSilicoResearchConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


async def test_extract_goals_node_captures_failure_into_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising ``_extract_goals`` lands in the failure_state callback.

    The inner coroutine raises a synthetic ``RuntimeError``; the
    workflow-boundary helper forwards it to ``failure_state``, which
    writes ``goals=[]`` and the stringified error into the returned
    state slice. The contract guarantees the workflow keeps running
    against a defined state shape instead of propagating an exception
    up through LangGraph.
    """
    agent = _build_agent()
    boom = RuntimeError("synthetic LLM outage")

    async def fake_extract(user_query: str, obs_file_list: list[str]) -> Any:
        """Raise the synthetic outage so failure_state captures it."""
        del user_query, obs_file_list
        raise boom

    monkeypatch.setattr(agent, "_extract_goals", fake_extract)

    result = await agent.extract_goals_node(
        cast(
            Any,
            {
                "paper_text": "PHYB regulation in rice under drought.",
                "obs_file_list": [],
            },
        )
    )

    assert result == {"goals": [], "error": "synthetic LLM outage"}


async def test_run_research_node_requires_output_dir() -> None:
    """A state without ``output_dir`` trips the loud guard, not a quiet
    fallback to ``None`` inside the analyst submit call.
    """
    agent = _build_agent()
    state = cast(
        Any,
        {
            "task_index": 0,
            "task_name": "research_goal_0",
            "goal_description": "Characterize PHYB",
            "context": "rice stress response",
            "data_list": {},
            "output_dir": None,
            "thread_id": "thread-c1",
        },
    )

    with pytest.raises(ValueError, match="output_dir is required"):
        await agent.run_research_node(state)


async def test_invalid_goal_extraction_makes_no_submit_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed goal extraction stops the graph before remote submission."""
    agent = _build_agent()

    async def invalid_extract(
        user_query: str,
        obs_file_list: list[str],
    ) -> list[dict[str, str]]:
        """Model the contract validator rejecting decoded goal JSON."""
        del user_query, obs_file_list
        raise ValueError("value must be nonblank")

    submit = AsyncMock()
    monkeypatch.setattr(agent, "_extract_goals", invalid_extract)
    monkeypatch.setattr(agent, "_submit_research_task", submit)

    result = await agent.app.ainvoke(
        {
            "paper_text": "A paper with an invalid empty objective.",
            "data_list": {},
            "user_id": "test-user",
            "obs_file_list": [],
            "output_dir": "/tmp/research-out",
            "goals": [],
            "research_tasks": [],
            "task_ids": {},
            "completed_count": 0,
            "error": None,
        },
        config={"configurable": {"thread_id": "invalid-goal-test"}},
    )

    assert result["goals"] == []
    assert result["error"] == "value must be nonblank"
    submit.assert_not_awaited()


async def test_arun_rejects_unpersistable_research_a2a_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2A pending work cannot escape as zero-task native running work."""
    agent = _build_agent()
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.research.agent.run_analysis_graph",
        AsyncMock(
            return_value={
                "task_ids": [],
                "error": None,
                "failures": [],
                "phytomni_state": {"a2a_pending": [{"task_id": "peer-task"}]},
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("paper", {})


async def test_arun_rejects_empty_research_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty result cannot escape as a successful Research submission."""
    agent = _build_agent()
    monkeypatch.setattr(
        research_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "task_ids": {},
                "error": None,
                "failures": [],
                "phytomni_state": {"submission_rejections": []},
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("paper", {})


async def test_arun_rejects_rejected_research_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded remote rejection cannot be projected as an accepted id."""
    agent = _build_agent()
    monkeypatch.setattr(
        research_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "task_ids": {},
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "submission_rejections": [
                        {
                            "goal": "root architecture",
                            "code": "upstream_rejected",
                        }
                    ]
                },
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("paper", {})


def test_research_submission_outcome_accepts_goal_task_mapping() -> None:
    """The private graph mapping classifies every nonblank remote id."""
    outcome = research_agent_module._research_submission_outcome(
        {
            "task_ids": {"goal-1": "task-1", "goal-2": "task-2"},
            "output_dir": "/tmp/research-out",
            "phytomni_state": {"submission_rejections": []},
        }
    )

    assert outcome.kind == "full"
    assert outcome.task_ids == ("task-1", "task-2")
    assert outcome.rejected == ()


async def test_arun_normalizes_task_ids_and_preserves_rejection_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public IDs and private state retain matching acceptance counts."""
    agent = _build_agent()
    state_task_ids = {"goal-1": "task-1", "goal-2": "task-2"}
    state_rejections = [
        {"goal": "root architecture", "code": "upstream_rejected"},
        {"goal": "drought response", "code": "upstream_timeout"},
    ]
    monkeypatch.setattr(
        research_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "task_ids": state_task_ids,
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "completed_count": 4,
                    "submission_rejections": state_rejections,
                },
            }
        ),
    )

    result = await agent.arun("paper", {})

    assert result["task_ids"] == ["task-1", "task-2"]
    accepted_count = len(result["task_ids"])
    rejected_count = len(result["phytomni_state"]["submission_rejections"])
    assert (
        accepted_count + rejected_count
        == result["phytomni_state"]["completed_count"]
    )
    assert result["submission_warnings"] == [
        {
            "code": "partial_submission",
            "retryable": False,
            "rejected_count": len(state_rejections),
        }
    ]
    assert result["submission_warnings"][0]["rejected_count"] == rejected_count


def test_research_mixed_a2a_pending_is_not_full_submission() -> None:
    """A local acceptance plus an unresolved A2A pause is partial."""
    outcome = research_agent_module._research_submission_outcome(
        {
            "task_ids": {"local": "local-task"},
            "phytomni_state": {
                "a2a_pending": [{"task_id": "peer-task"}],
            },
        }
    )

    assert outcome.kind == "partial"
    assert outcome.task_ids == ("local-task",)
    assert outcome.warnings[0]["rejected_count"] == 1


async def test_research_dispatch_propagates_missing_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local task-id invariant must not become an upstream warning."""
    agent = _build_agent()
    monkeypatch.setattr(
        agent,
        "_submit_research_task",
        AsyncMock(
            side_effect=RemoteAnalysisSubmissionError("missing task id")
        ),
    )
    state = cast(
        Any,
        {
            "task_index": 0,
            "task_name": "research_goal_0",
            "goal_description": "Characterize PHYB",
            "context": "rice stress response",
            "data_list": {},
            "output_dir": "/tmp/research-out",
            "thread_id": "thread-c2",
        },
    )

    with pytest.raises(RemoteAnalysisSubmissionError, match="missing task"):
        await agent.run_research_node(state)
