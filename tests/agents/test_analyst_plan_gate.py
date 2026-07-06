# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the analyst plan-check PLAN_MIN_SCORE gate.

Pin the gate contract on the wired ``check_post_node`` /
``check_prep_node`` split: PLAN_MIN_SCORE == 0 approves on APPROVED or
on retry exhaustion regardless of score; PLAN_MIN_SCORE > 0 fails loudly
via McpError once retries exhaust below the threshold instead of
force-approving, while the preset-plan fast path stays unconditional in
the prep node.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst.agent import AnalystAgentsState
from mcp_server_phytomni.agents.analyst.graph_chat_subgraph import (
    AnalystChatSubgraphMixin,
)
from mcp_server_phytomni.config.defaults import AnalystConfig
from tests.agents._analyst_fakes import fake_analyst_sensitive_config

pytestmark = pytest.mark.unit


def _fake_self(plan_min_score: int, max_retries: int) -> SimpleNamespace:
    """Build a duck-typed gate host with the two tuned config fields.

    Args:
        plan_min_score: PLAN_MIN_SCORE override for the analyst config.
        max_retries: MAX_RETRIES override for the analyst config.

    Returns:
        Object exposing analyst_config and sensitive_config.
    """
    return SimpleNamespace(
        analyst_config=AnalystConfig(
            PLAN_MIN_SCORE=plan_min_score,
            MAX_RETRIES=max_retries,
        ),
        sensitive_config=fake_analyst_sensitive_config(),
    )


def _verdict_state(
    retries: int, score: int, decision: str, feedback: str
) -> AnalystAgentsState:
    """Build a check_post_node state carrying one critic verdict.

    The shared chat node already ran, so the verdict arrives under
    ``chat_response`` rather than from a fresh ``phyto_chat`` call; a
    non-None ``chat_payload`` marks the non-auto-approve path the post
    node evaluates (the auto-approve path returns ``{}`` early).

    Args:
        retries: Value for plan_retries.
        score: Critic score the upstream chat returned.
        decision: Critic decision string the upstream chat returned.
        feedback: Critic feedback string the upstream chat returned.

    Returns:
        The workflow state mapping passed to check_post_node.
    """
    content = json.dumps(
        {"score": score, "decision": decision, "feedback": feedback}
    )
    state: dict[str, Any] = {
        "plan_retries": retries,
        "chat_payload": {"staged": True},
        "chat_response": {"choices": [{"message": {"content": content}}]},
    }
    return cast(AnalystAgentsState, state)


async def test_default_zero_approves_on_decision() -> None:
    """PLAN_MIN_SCORE=0 approves an APPROVED verdict despite low score."""
    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(0, 5), _verdict_state(0, 20, "APPROVED", "f")
    )
    assert result == {"plan_feedback": "APPROVED"}


async def test_default_zero_force_approves_on_exhaustion() -> None:
    """PLAN_MIN_SCORE=0 still force-approves once retries exhaust."""
    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(0, 5), _verdict_state(5, 30, "REJECTED", "f")
    )
    assert result == {"plan_feedback": "APPROVED"}


async def test_default_zero_returns_feedback_before_exhaustion() -> None:
    """PLAN_MIN_SCORE=0 returns feedback when not yet exhausted."""
    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(0, 5), _verdict_state(1, 30, "REJECTED", "fix")
    )
    assert result == {"plan_feedback": "fix"}


async def test_threshold_approves_when_score_meets() -> None:
    """A score at/above PLAN_MIN_SCORE with APPROVED is approved."""
    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(70, 5), _verdict_state(0, 80, "APPROVED", "f")
    )
    assert result == {"plan_feedback": "APPROVED"}


async def test_threshold_raises_mcperror_on_exhausted_low_score() -> None:
    """Exhausting retries below PLAN_MIN_SCORE fails loudly."""
    with pytest.raises(McpError) as excinfo:
        await AnalystChatSubgraphMixin.check_post_node(
            _fake_self(70, 3), _verdict_state(3, 50, "APPROVED", "bad")
        )
    message = excinfo.value.error.message
    assert "PLAN_MIN_SCORE 70" in message
    assert "50" in message
    assert "bad" in message


async def test_threshold_retries_when_low_score_not_exhausted() -> None:
    """A low score before exhaustion returns feedback to retry."""
    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(70, 5), _verdict_state(0, 50, "APPROVED", "redo")
    )
    assert result == {"plan_feedback": "redo"}


async def test_preset_plan_fast_path_ignores_threshold() -> None:
    """The preset/reset-plan fast path approves in the prep node.

    ``check_prep_node`` short-circuits to APPROVED with
    ``chat_payload=None`` for a preset plan that carries no method
    context, so the gate threshold never runs.
    """
    state = cast(
        AnalystAgentsState,
        {
            "goal_description": "goal",
            "data_list": {},
            "method_context": None,
            "plan": "plan",
            "plan_retries": 0,
            "is_preset_plan": True,
        },
    )
    result = await AnalystChatSubgraphMixin.check_prep_node(
        _fake_self(90, 5), state
    )
    assert result["plan_feedback"] == "APPROVED"
    assert result["chat_payload"] is None
