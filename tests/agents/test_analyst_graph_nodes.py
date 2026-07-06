# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the analyst plan/check prep + post nodes.

Complements test_analyst_plan_gate.py (which pins the PLAN_MIN_SCORE
threshold on ``check_post_node``): this file pins ``plan_prep_node``
prompt-template branching, ``plan_post_node`` response handling, and
``check_post_node``'s malformed-JSON defensive path. The chat call runs
in the shared chat node, so the tests feed the verdict through
``chat_response`` and drive the wired prep/post methods directly.
"""

# pylint: disable=protected-access
# Test file exercises the analyst graph-mixin's internal helper
# ``_submit_output_dir`` directly; pylint W0212 is suppressed at file
# scope because the unit test must reach the smallest sub-operation
# that forwards the fingerprint. See ``docs/lint-exemptions.md``.

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.shared.exceptions import McpError

import mcp_server_phytomni.agents.analyst.graph as analyst_graph
from mcp_server_phytomni.agents.analyst import (
    graph_chat_subgraph as analyst_chat,
)
from mcp_server_phytomni.agents.analyst.agent import AnalystAgentsState
from mcp_server_phytomni.agents.analyst.graph import AnalystGraphMixin
from mcp_server_phytomni.agents.analyst.graph_chat_subgraph import (
    AnalystChatSubgraphMixin,
)
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.storage.path_policy import IdFactory, RunIdentity
from tests.agents._analyst_fakes import fake_analyst_sensitive_config

pytestmark = pytest.mark.agent


def _fake_self() -> SimpleNamespace:
    """Build a duck-typed plan/check prep + post host."""
    return SimpleNamespace(
        analyst_config=AnalystConfig(),
        sensitive_config=fake_analyst_sensitive_config(),
    )


def _capture_prompt_paths(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub graph_chat_subgraph.get_prompt; capture prompt path keys.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Mutable list collecting the ``prompt_path`` value passed to
        every ``get_prompt`` call in test order.
    """
    prompt_paths: list[str] = []

    def fake_prompt(
        _file: str, prompt_path: str, _args: dict[str, Any]
    ) -> str:
        prompt_paths.append(prompt_path)
        return f"PROMPT::{prompt_path}"

    monkeypatch.setattr(analyst_chat, "get_prompt", fake_prompt)
    return prompt_paths


def _plan_state(
    *, plan_feedback: str | None, obs_file_list: list[str]
) -> AnalystAgentsState:
    """Build a ``plan_prep_node`` input state with the branching keys.

    Args:
        plan_feedback: ``plan_feedback`` value (None means initial plan
            generation; non-empty triggers the revise branch).
        obs_file_list: ``obs_file_list`` value (non-empty triggers the
            file-aware prompt variant).

    Returns:
        The workflow state mapping passed to ``plan_prep_node``.
    """
    state: dict[str, Any] = {
        "goal_description": "goal",
        "obs_file_list": obs_file_list,
        "method_context": {
            "retrieve_context": "ctx",
            "upload_context": "uploads",
        },
        "plan": "raw",
        "plan_feedback": plan_feedback,
    }
    return cast(AnalystAgentsState, state)


def _chat_response_state(
    content: str | None, plan_retries: int = 0
) -> AnalystAgentsState:
    """Build a ``plan_post_node`` state carrying one chat response.

    Args:
        content: Assistant content the shared chat node returned;
            ``None`` means the message has no usable content (drives the
            empty-response error path).
        plan_retries: Current ``plan_retries`` counter.

    Returns:
        The workflow state mapping passed to ``plan_post_node``.
    """
    message: dict[str, Any] = {}
    if content is not None:
        message["content"] = content
    state: dict[str, Any] = {
        "plan_retries": plan_retries,
        "chat_response": {"choices": [{"message": message}]},
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
async def test_plan_prep_node_selects_prompt_by_state(
    monkeypatch: pytest.MonkeyPatch,
    plan_feedback: str | None,
    obs_file_list: list[str],
    expected_path: str,
) -> None:
    """The four (feedback x obs_file_list) combos each pick one template.

    Pins the user-facing prompt-template selection that callers cannot
    inspect from the public surface: a wrong template would silently
    change plan quality without any error. Asserting the exact captured
    path (not a substring of the rendered text, which every template
    shares) keeps the four templates distinguishable.
    """
    prompt_paths = _capture_prompt_paths(monkeypatch)
    state = _plan_state(
        plan_feedback=plan_feedback, obs_file_list=obs_file_list
    )

    await AnalystChatSubgraphMixin.plan_prep_node(_fake_self(), state)

    assert prompt_paths == [expected_path]


async def test_plan_post_node_increments_retries_and_clears_feedback() -> None:
    """plan_post_node returns retries+1 and drops the prior feedback.

    The retry counter feeds the check gate's threshold loop, and
    dropping the feedback prevents the revise branch from being chosen
    twice in a row once the plan has actually been revised.
    """
    result = await AnalystChatSubgraphMixin.plan_post_node(
        _fake_self(), _chat_response_state("next plan", plan_retries=2)
    )

    assert result == {
        "plan": "next plan",
        "plan_retries": 3,
        "plan_feedback": None,
    }


async def test_plan_post_node_raises_when_llm_returns_empty_content() -> None:
    """Empty assistant content raises a sanitized INTERNAL_ERROR McpError.

    Drives the defensive branch in plan_post_node: the shared chat node
    occasionally yields a choice whose message lacks a ``content``
    field. The node must reject the chat response instead of forwarding
    an empty plan to the downstream tool-extract / submit nodes.
    """
    with pytest.raises(McpError) as excinfo:
        await AnalystChatSubgraphMixin.plan_post_node(
            _fake_self(), _chat_response_state(None)
        )

    assert "Failed to generate plan" in excinfo.value.error.message


async def test_check_post_node_treats_malformed_json_as_rejected() -> None:
    """Bad critic JSON degrades to score=0, REJECTED, empty feedback.

    Pins the try/except in check_post_node: when ``json.loads`` (or the
    fenced ```json extractor) fails, the node must still return a
    ``plan_feedback`` value rather than propagating the parse error.
    With PLAN_MIN_SCORE=0 and retries available, the degradation
    surfaces as an empty ``plan_feedback`` string driving another revise
    loop.
    """
    state = cast(
        AnalystAgentsState,
        {
            "plan_retries": 1,
            "chat_payload": {"staged": True},
            "chat_response": {
                "choices": [{"message": {"content": "not-json-at-all"}}]
            },
        },
    )

    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(), state
    )

    assert result == {"plan_feedback": ""}


def test_submit_output_dir_forwards_input_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_submit_output_dir`` passes ``input_fingerprint`` to
    ``ensure_run_output_dir`` so the created directory routes to the
    content-addressed shared key rather than a per-run user-scoped path.

    A fingerprint in state is the integration point between the dedup
    pipeline (which computes it in ``retrieve_plan_submit``) and the OBS
    directory creator (which routes on it in ``create_output_dir``).
    """
    captured: dict[str, Any] = {}

    def _fake_ensure(
        config: Any,
        sensitive_config: Any,
        task: str,
        run_identity: Any,
        output_dir: str | None = None,
        **kwargs: Any,
    ) -> str:
        del config, sensitive_config, task, run_identity, output_dir
        captured["fingerprint"] = kwargs.get("fingerprint")
        return "/obs/phytomni/agent_data/shared/fp/output/"

    monkeypatch.setattr(analyst_graph, "ensure_run_output_dir", _fake_ensure)

    config = AnalystConfig()
    fake_self = SimpleNamespace(
        analyst_config=config,
        sensitive_config=fake_analyst_sensitive_config(),
    )
    run_identity = RunIdentity(
        user_id="alice",
        run_id=IdFactory().new_id("run", "analysis_agents_task"),
        created_at=datetime(2026, 6, 16, tzinfo=UTC),
    )
    state = cast(
        AnalystAgentsState,
        {"input_fingerprint": "f" * 64, "output_dir": ""},
    )

    AnalystGraphMixin._submit_output_dir(fake_self, state, run_identity)

    assert captured["fingerprint"] == "f" * 64
