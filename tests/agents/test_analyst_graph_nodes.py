# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for AnalystGraphMixin plan and check nodes.

Complements test_analyst_plan_gate.py: that file pins the PLAN_MIN_SCORE
threshold logic; this one pins plan_node prompt-template branching and
the check_node defensive path when the critic LLM returns malformed
JSON. Both files monkeypatch graph.phyto_chat / graph.get_prompt and
exercise the bound mixin methods directly without booting LangGraph.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst import graph as analyst_graph
from mcp_server_phytomni.agents.analyst.agent import AnalystAgentsState
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.config.defaults import AnalystConfig
from tests.agents._analyst_fakes import fake_analyst_sensitive_config

pytestmark = pytest.mark.agent


def _fake_self() -> SimpleNamespace:
    """Build a duck-typed plan/check_node host."""
    return SimpleNamespace(
        analyst_config=AnalystConfig(),
        sensitive_config=fake_analyst_sensitive_config(),
    )


def _patch_chat(
    monkeypatch: pytest.MonkeyPatch, content: str | None
) -> list[str]:
    """Stub graph.phyto_chat / graph.get_prompt; capture prompt path keys.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        content: Assistant content the fake LLM returns; ``None`` means
            the message has no usable content (drives the empty-response
            error path).

    Returns:
        Mutable list collecting the ``prompt_path`` value passed to
        every ``get_prompt`` call in test order.
    """
    prompt_paths: list[str] = []

    def fake_prompt(
        _file: str, prompt_path: str, _args: Dict[str, Any]
    ) -> str:
        prompt_paths.append(prompt_path)
        return f"PROMPT::{prompt_path}"

    async def fake_chat(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        message: Dict[str, Any] = {}
        if content is not None:
            message["content"] = content
        return {"choices": [{"message": message}]}

    monkeypatch.setattr(analyst_graph, "get_prompt", fake_prompt)
    monkeypatch.setattr(analyst_graph, "phyto_chat", fake_chat)
    return prompt_paths


def _plan_state(
    *,
    plan_feedback: str | None,
    obs_file_list: list[str],
    plan_retries: int = 0,
) -> AnalystAgentsState:
    """Build a plan_node input state with the three branching keys.

    Args:
        plan_feedback: ``plan_feedback`` value (None means initial plan
            generation; non-empty triggers the revise branch).
        obs_file_list: ``obs_file_list`` value (non-empty triggers the
            file-aware prompt variant).
        plan_retries: Current ``plan_retries`` counter.

    Returns:
        The workflow state mapping passed to ``plan_node``.
    """
    state: Dict[str, Any] = {
        "goal_description": "goal",
        "obs_file_list": obs_file_list,
        "method_context": {
            "retrieve_context": "ctx",
            "upload_context": "uploads",
        },
        "plan": "raw",
        "plan_feedback": plan_feedback,
        "plan_retries": plan_retries,
    }
    return cast(AnalystAgentsState, state)


@pytest.mark.parametrize(
    ("plan_feedback", "obs_file_list", "expected_path"),
    [
        (None, [], "user/analysis_retrieve"),
        (None, ["/obs/phytomni/x.pdf"], "user/analysis_retrieve_file"),
        ("redo", [], "user/analysis_retrieve_feedback"),
        (
            "redo",
            ["/obs/phytomni/x.pdf"],
            "user/analysis_retrieve_file_feedback",
        ),
    ],
)
async def test_plan_node_selects_prompt_by_state(
    monkeypatch: pytest.MonkeyPatch,
    plan_feedback: str | None,
    obs_file_list: list[str],
    expected_path: str,
) -> None:
    """The four (feedback × obs_file_list) combinations each pick one prompt.

    Pins the user-facing prompt-template selection that callers cannot
    inspect from the public surface: the node still returns ``{plan,
    plan_retries, plan_feedback}``, but a wrong prompt template would
    silently change the plan quality without any error.
    """
    prompt_paths = _patch_chat(monkeypatch, content="generated plan")
    state = _plan_state(
        plan_feedback=plan_feedback, obs_file_list=obs_file_list
    )

    await AnalystGraphMixin.plan_node(_fake_self(), state)

    assert prompt_paths == [expected_path]


async def test_plan_node_increments_retries_and_clears_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_node always returns retries+1 and drops the prior feedback.

    The retry counter feeds the check_node threshold loop, and dropping
    the feedback prevents the revise branch from being chosen twice in
    a row once the plan has actually been revised.
    """
    _patch_chat(monkeypatch, content="next plan")
    state = _plan_state(
        plan_feedback="prior", obs_file_list=[], plan_retries=2
    )

    result = await AnalystGraphMixin.plan_node(_fake_self(), state)

    assert result == {
        "plan": "next plan",
        "plan_retries": 3,
        "plan_feedback": None,
    }


async def test_plan_node_raises_when_llm_returns_empty_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty assistant content raises a sanitized INTERNAL_ERROR McpError.

    Drives the defensive branch at the bottom of plan_node: the LLM
    occasionally returns a choice whose message lacks a ``content``
    field. The node must reject the chat response instead of forwarding
    an empty plan to the downstream tool-extract / submit nodes.
    """
    _patch_chat(monkeypatch, content=None)
    state = _plan_state(plan_feedback=None, obs_file_list=[])

    with pytest.raises(McpError) as excinfo:
        await AnalystGraphMixin.plan_node(_fake_self(), state)

    assert "Failed to generate plan" in excinfo.value.error.message


async def test_check_node_treats_malformed_json_as_rejected_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad critic JSON degrades to score=0, REJECTED, empty feedback.

    Pins the try/except in check_node: when ``json.loads`` (or the
    ```json fenced block extractor) fails, the node must still return
    a ``plan_feedback`` value rather than propagating a TypeError or
    JSONDecodeError into the LangGraph runner. With PLAN_MIN_SCORE=0
    and retries still available, the degradation surfaces as an empty
    ``plan_feedback`` string driving another revise loop.
    """
    monkeypatch.setattr(
        analyst_graph, "get_prompt", lambda *_a, **_k: "PROMPT"
    )

    async def fake_chat(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return {"choices": [{"message": {"content": "not-json-at-all"}}]}

    monkeypatch.setattr(analyst_graph, "phyto_chat", fake_chat)

    state = cast(
        AnalystAgentsState,
        {
            "goal_description": "goal",
            "data_list": {},
            "method_context": "ctx",
            "plan": "plan",
            "plan_retries": 1,
            "is_preset_plan": False,
        },
    )

    result = await AnalystGraphMixin.check_node(_fake_self(), state)

    assert result == {"plan_feedback": ""}
