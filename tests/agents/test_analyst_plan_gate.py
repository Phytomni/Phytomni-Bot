# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the analyst plan-check PLAN_MIN_SCORE gate.

Pin the C-2 decision-C contract: with PLAN_MIN_SCORE == 0 the check
node behaves byte-for-byte as before (approve on APPROVED or on retry
exhaustion regardless of score); with PLAN_MIN_SCORE > 0 a plan whose
critic score stays below the threshold until retries exhaust fails
loudly via McpError instead of being force-approved, while the
preset-plan fast path stays unconditional.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst import graph as analyst_graph
from mcp_server_phytomni.agents.analyst.agent import AnalystAgentsState
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.config.defaults import AnalystConfig

pytestmark = pytest.mark.unit


def _fake_self(plan_min_score: int, max_retries: int) -> SimpleNamespace:
    """Build a duck-typed check_node host with the two tuned fields.

    Args:
        plan_min_score: PLAN_MIN_SCORE override for the analyst config.
        max_retries: MAX_RETRIES override for the analyst config.

    Returns:
        Object exposing analyst_config and sensitive_config.
    """
    analyst_config = AnalystConfig(
        PLAN_MIN_SCORE=plan_min_score,
        MAX_RETRIES=max_retries,
    )
    sensitive_config = SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=lambda: "k"),
        BASE_URL="http://example.invalid",
        MODEL_ID="model",
    )
    return SimpleNamespace(
        analyst_config=analyst_config,
        sensitive_config=sensitive_config,
    )


def _patch_chat(
    monkeypatch: pytest.MonkeyPatch,
    score: int,
    decision: str,
    feedback: str,
) -> None:
    """Stub graph.phyto_chat / graph.get_prompt for one critic verdict.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        score: Critic score the fake LLM returns.
        decision: Critic decision string the fake LLM returns.
        feedback: Critic feedback string the fake LLM returns.
    """
    content = json.dumps(
        {"score": score, "decision": decision, "feedback": feedback}
    )

    async def fake_chat(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Return one critic verdict shaped like a chat completion.

        Args:
            *args: Ignored positional args.
            **kwargs: Ignored keyword args.

        Returns:
            A minimal chat-completion payload carrying the verdict.
        """
        _ = (args, kwargs)
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(analyst_graph, "phyto_chat", fake_chat)
    monkeypatch.setattr(
        analyst_graph, "get_prompt", lambda *_a, **_k: "PROMPT"
    )


def _state(retries: int, **overrides: Any) -> AnalystAgentsState:
    """Build a non-preset check_node state with a retry count.

    Args:
        retries: Value for plan_retries.
        **overrides: Extra state keys (e.g. is_preset_plan).

    Returns:
        The workflow state mapping passed to check_node.
    """
    state: Dict[str, Any] = {
        "goal_description": "goal",
        "data_list": {},
        "method_context": "ctx",
        "plan": "plan",
        "plan_retries": retries,
        "is_preset_plan": False,
    }
    state.update(overrides)
    return cast(AnalystAgentsState, state)


async def test_default_zero_approves_on_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN_MIN_SCORE=0 approves an APPROVED verdict despite low score.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the approval assertion passes.
    """
    _patch_chat(monkeypatch, score=20, decision="APPROVED", feedback="f")
    result = await AnalystGraphMixin.check_node(_fake_self(0, 5), _state(0))
    assert result == {"plan_feedback": "APPROVED"}


async def test_default_zero_force_approves_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN_MIN_SCORE=0 still force-approves once retries exhaust.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the force-approval assertion passes.
    """
    _patch_chat(monkeypatch, score=30, decision="REJECTED", feedback="f")
    result = await AnalystGraphMixin.check_node(_fake_self(0, 5), _state(5))
    assert result == {"plan_feedback": "APPROVED"}


async def test_default_zero_returns_feedback_before_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN_MIN_SCORE=0 returns feedback when not yet exhausted.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the feedback assertion passes.
    """
    _patch_chat(monkeypatch, score=30, decision="REJECTED", feedback="fix")
    result = await AnalystGraphMixin.check_node(_fake_self(0, 5), _state(1))
    assert result == {"plan_feedback": "fix"}


async def test_threshold_approves_when_score_meets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A score at/above PLAN_MIN_SCORE with APPROVED is approved.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the approval assertion passes.
    """
    _patch_chat(monkeypatch, score=80, decision="APPROVED", feedback="f")
    result = await AnalystGraphMixin.check_node(_fake_self(70, 5), _state(0))
    assert result == {"plan_feedback": "APPROVED"}


async def test_threshold_raises_mcperror_on_exhausted_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting retries below PLAN_MIN_SCORE fails loudly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the McpError content assertions pass.
    """
    _patch_chat(monkeypatch, score=50, decision="APPROVED", feedback="bad")
    with pytest.raises(McpError) as excinfo:
        await AnalystGraphMixin.check_node(_fake_self(70, 3), _state(3))
    message = excinfo.value.error.message
    assert "PLAN_MIN_SCORE 70" in message
    assert "50" in message
    assert "bad" in message


async def test_threshold_retries_when_low_score_not_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A low score before exhaustion returns feedback to retry.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the feedback assertion passes.
    """
    _patch_chat(monkeypatch, score=50, decision="APPROVED", feedback="redo")
    result = await AnalystGraphMixin.check_node(_fake_self(70, 5), _state(0))
    assert result == {"plan_feedback": "redo"}


async def test_preset_plan_fast_path_ignores_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preset/reset-plan fast path approves regardless of gate.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None after the unconditional approval assertion passes.
    """
    _patch_chat(monkeypatch, score=0, decision="REJECTED", feedback="x")
    state = _state(0, is_preset_plan=True, method_context=None)
    result = await AnalystGraphMixin.check_node(_fake_self(90, 5), state)
    assert result == {"plan_feedback": "APPROVED"}
