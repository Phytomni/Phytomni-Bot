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

# The two direct helper probes below intentionally target the smallest
# analyst graph operations; each carries a symbol-scoped directive.

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


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{"message": "not-a-mapping"}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_plan_post_node_rejects_malformed_response_shapes(
    response: Any,
) -> None:
    """Every unusable OpenAI response keeps the plan failure contract."""
    state = cast(
        AnalystAgentsState,
        {"plan_retries": 0, "chat_response": response},
    )

    with pytest.raises(McpError, match="Failed to generate plan"):
        await AnalystChatSubgraphMixin.plan_post_node(_fake_self(), state)


async def test_parse_query_post_node_uses_common_response_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse-query keeps domain fields while using canonical response walks."""
    captured: dict[str, Any] = {}

    def fake_message_content(response: Any) -> str:
        captured["response"] = response
        return "ignored"

    def fake_parse_json_object(text: str) -> dict[str, Any]:
        captured["text"] = text
        return {
            "goal_description": "goal",
            "data_list": '{"/obs/data.tsv": "data"}',
            "plan": "plan",
        }

    monkeypatch.setattr(analyst_chat, "message_content", fake_message_content)
    monkeypatch.setattr(
        analyst_chat,
        "parse_json_object_fragment",
        fake_parse_json_object,
    )
    response = {"choices": [{"message": {"content": "payload"}}]}
    state = cast(
        AnalystAgentsState,
        {"chat_payload": {"staged": True}, "chat_response": response},
    )

    result = await AnalystChatSubgraphMixin.parse_query_post_node(
        _fake_self(), state
    )

    assert captured == {"response": response, "text": "ignored"}
    assert result == {
        "goal_description": "goal",
        "data_list": {"/obs/data.tsv": "data"},
        "plan": "plan",
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"goal_description": "goal", "data_list": "{}", "plan": "plan"}',
        (
            '```json\n{"goal_description": "goal", '
            '"data_list": "{}", "plan": "plan"}\n```'
        ),
    ],
)
async def test_parse_query_post_node_accepts_object_fragments(
    content: str,
) -> None:
    """Plain and fenced objects retain parse-query's domain projection."""
    state = cast(
        AnalystAgentsState,
        {
            "chat_payload": {"staged": True},
            "chat_response": {"choices": [{"message": {"content": content}}]},
        },
    )

    result = await AnalystChatSubgraphMixin.parse_query_post_node(
        _fake_self(), state
    )

    assert result == {
        "goal_description": "goal",
        "data_list": {},
        "plan": "plan",
    }


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '{"selected_data": {"/obs/selected.tsv": "selected"}}',
            {"/obs/selected.tsv": "selected"},
        ),
        (
            '```json\n{"selected_data": {"/obs/fenced.tsv": "fenced"}}\n```',
            {"/obs/fenced.tsv": "fenced"},
        ),
    ],
)
async def test_data_select_post_node_accepts_object_fragments(
    content: str, expected: dict[str, str]
) -> None:
    """Data selection keeps the selected-data merge around common parsing."""
    state = cast(
        AnalystAgentsState,
        {
            "data_list": {"/obs/original.tsv": "original"},
            "chat_response": {"choices": [{"message": {"content": content}}]},
        },
    )

    result = await AnalystChatSubgraphMixin.data_select_post_node(
        _fake_self(), state
    )

    assert result == {
        "data_list": {"/obs/original.tsv": "original", **expected}
    }


def _make_agent(**config_overrides: Any) -> SimpleNamespace:
    """Build a duck-typed analyst host with explicit config overrides."""
    return SimpleNamespace(
        analyst_config=AnalystConfig(**config_overrides),
        sensitive_config=fake_analyst_sensitive_config(),
    )


async def _capture_create_payload(agent: Any) -> dict[str, Any]:
    """Build the submit payload without contacting the remote platform."""
    state = cast(AnalystAgentsState, {"compute_resource": "small"})
    _, payload = getattr(AnalystGraphMixin, "_submit_job_data")(
        agent,
        state,
        "/obs/task.yaml",
        "/obs/model.yaml",
    )
    return payload


async def test_submit_payload_uses_remote_job_timeout() -> None:
    """Remote create-task payload uses the dedicated job timeout budget."""
    agent = _make_agent(
        TIMEOUT=11,
        MAX_POLL=22,
        ANALYSIS_JOB_TIMEOUT=33,
    )

    payload = await _capture_create_payload(agent)

    assert payload["timeout"] == 33


async def test_relay_submit_payload_uses_compute_selector_without_app_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relay child selects a tier without knowing the operator app UUID."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "relay-key")
    agent = _make_agent(APP_ID={})

    payload = await _capture_create_payload(agent)

    assert payload["compute_resource"] == "small"
    assert "tool_id" not in payload


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


@pytest.mark.parametrize(
    "content",
    [
        '{"score": 1, "decision": "APPROVED", "feedback": ""}',
        '```json\n{"score": 1, "decision": "APPROVED", "feedback": ""}\n```',
    ],
)
async def test_check_post_node_accepts_object_fragments(content: str) -> None:
    """Valid critic objects retain the approval decision."""
    state = cast(
        AnalystAgentsState,
        {
            "plan_retries": 1,
            "chat_payload": {"staged": True},
            "chat_response": {"choices": [{"message": {"content": content}}]},
        },
    )

    result = await AnalystChatSubgraphMixin.check_post_node(
        _fake_self(), state
    )

    assert result == {"plan_feedback": "APPROVED"}


@pytest.mark.parametrize(
    "content",
    [
        '{"tools": ["tool_a", "tool_b"]}',
        '```json\n{"tools": ["tool_fenced"]}\n```',
    ],
)
async def test_tool_extract_post_node_accepts_object_fragments(
    content: str,
) -> None:
    """Tool extraction keeps its list projection around common parsing."""
    state = cast(
        AnalystAgentsState,
        {
            "chat_response": {"choices": [{"message": {"content": content}}]},
        },
    )

    result = await AnalystChatSubgraphMixin.tool_extract_post_node(
        _fake_self(), state
    )

    assert result == {
        "extracted_tools": (
            ["tool_a", "tool_b"] if "tool_a" in content else ["tool_fenced"]
        )
    }


async def test_submit_output_dir_forwards_input_fingerprint(
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

    async def _fake_ensure(
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

    await getattr(AnalystGraphMixin, "_submit_output_dir")(
        fake_self, state, run_identity
    )

    assert captured["fingerprint"] == "f" * 64
