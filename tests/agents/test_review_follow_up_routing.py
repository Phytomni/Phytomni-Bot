# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Per-call-site tests for ReviewAgent chat-subgraph follow-up routing.

Pins the five ReviewAgent chat-subgraph call sites' explicit
``with_follow_up`` values (four ``False`` prep / fan-out sites and the
terminal ``follow_up_prep_node`` site ``True``) plus the tri-state
contract on :func:`build_chat_kwargs_for` (``None`` omits the key so
non-review consumers keep their existing router default).
"""

# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.types import Send

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.graphs.chat_adapters import build_chat_kwargs_for

pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for prep-node assertions.

    The prep / route helpers stage a ``chat_payload`` carrying the
    explicit ``with_follow_up`` value for the shared chat subgraph.
    """
    config = ReviewConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": False,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _fake_config() -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for the chat-kwargs builder.

    Mirrors ``tests/agents/test_chat_adapters.py`` so the tri-state
    ``with_follow_up`` assertions stay independent of Pydantic
    validation on the real ``ReviewAgentConfig`` instance.
    """
    return SimpleNamespace(
        PROMPT_FILE="prompt.yaml",
        PROMPT_PATH="/tmp/prompts",
        FREQUENCY_PENALTY=0.0,
        N=1,
        PRESENCE_PENALTY=0.0,
        REASONING_EFFORT="medium",
        RESPONSE_FORMAT={"type": "text"},
        STREAM=False,
        TEMPERATURE=0.2,
        TOP_P=0.9,
        USER="consumer-user",
        TIMEOUT=120.0,
        RETRIABLE_CODES=[429, 500, 502, 503, 504],
        MAX_RETRIES=3,
    )


def _fake_sensitive_config() -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for ``SensitiveConfig``.

    ``API_KEY`` exposes ``get_secret_value`` to mirror the real
    Pydantic ``SecretStr`` surface every consumer agent reads.
    """
    return SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=lambda: "sk-test"),
        BASE_URL="https://llm.example/v1",
        MODEL_ID="phyto-llm-v1",
    )


# ---------------------------------------------------------------------------
# Per-site prep-node assertions.
# ---------------------------------------------------------------------------


async def test_plan_query_prep_chat_kwargs_disables_follow_up() -> None:
    """``plan_query_prep_node`` stages ``with_follow_up`` False."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "How does photosynthesis work?",
            "obs_file_list": [],
        },
    )
    result = await agent.plan_query_prep_node(state)
    chat_kwargs = result["chat_payload"]["chat_kwargs"]
    assert chat_kwargs["with_follow_up"] is False


async def test_summary_prep_chat_kwargs_disables_follow_up() -> None:
    """``summary_prep_node`` stages ``with_follow_up`` False."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "revised_reports": [
                {"subtopic": "dim1", "revised_report": "Content 1"},
            ],
            "research_dimensions": ["dim1"],
        },
    )
    result = await agent.summary_prep_node(state)
    chat_kwargs = result["chat_payload"]["chat_kwargs"]
    assert chat_kwargs["with_follow_up"] is False


async def test_follow_up_prep_chat_kwargs_enables_follow_up() -> None:
    """``follow_up_prep_node`` stages ``with_follow_up`` True.

    This is the one ReviewAgent chat-subgraph site whose chat call
    should emit follow-up questions — the terminal, user-facing
    summary stage that renders into ``final_response``.
    """
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "A review of photosynthesis.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
        },
    )
    result = await agent.follow_up_prep_node(state)
    chat_kwargs = result["chat_payload"]["chat_kwargs"]
    assert chat_kwargs["with_follow_up"] is True


# ---------------------------------------------------------------------------
# Per-Send-payload route-fan-out assertions.
# ---------------------------------------------------------------------------


def test_route_draft_tasks_payload_disables_follow_up() -> None:
    """Every ``route_draft_tasks`` Send carries ``with_follow_up`` False."""
    agent = _build_agent()
    params = [
        {"subtopic": "photosynthesis", "knowledge": "snippet-0"},
        {"subtopic": "chlorophyll", "knowledge": "snippet-1"},
        {"subtopic": "stomatal", "knowledge": "snippet-2"},
    ]
    state = cast(DeepResearchState, {"dimension_params": params})
    sends = agent.route_draft_tasks(state)

    assert len(sends) == 3
    for send in sends:
        assert isinstance(send, Send)
        assert send.node == "draft_worker_node"
        chat_kwargs = send.arg["chat_payload"]["chat_kwargs"]
        assert chat_kwargs["with_follow_up"] is False


def test_route_review_results_tasks_payload_disables_follow_up() -> None:
    """``route_review_results_tasks`` Sends carry ``with_follow_up`` False."""
    agent = _build_agent()
    dimensions = ["photosynthesis", "chlorophyll", "stomatal"]
    drafts = ["draft-A", "draft-B", "draft-C"]
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": dimensions,
            "draft_contents": drafts,
        },
    )
    sends = agent.route_review_results_tasks(state)

    assert len(sends) == 3
    for send in sends:
        assert isinstance(send, Send)
        assert send.node == "review_results_worker_node"
        chat_kwargs = send.arg["chat_payload"]["chat_kwargs"]
        assert chat_kwargs["with_follow_up"] is False


# ---------------------------------------------------------------------------
# Shared adapter tri-state contract.
# ---------------------------------------------------------------------------


def test_build_chat_kwargs_for_default_omits_follow_up_key() -> None:
    """Default ``with_follow_up=None`` omits the key for back-compat.

    Existing non-review consumers (analyst / knowledge / data) call
    ``build_chat_kwargs_for`` without the kwarg; the returned bag must
    not silently flip their chat-subgraph mounts to ``False`` and so
    must omit the key entirely.
    """
    bag: dict[str, Any] = build_chat_kwargs_for(
        config=_fake_config(),
        sensitive_config=_fake_sensitive_config(),
    )
    assert "with_follow_up" not in bag


def test_build_chat_kwargs_for_explicit_false_includes_follow_up_key() -> None:
    """Explicit ``with_follow_up=False`` includes the key with value False."""
    bag: dict[str, Any] = build_chat_kwargs_for(
        config=_fake_config(),
        sensitive_config=_fake_sensitive_config(),
        with_follow_up=False,
    )
    assert bag["with_follow_up"] is False


def test_build_chat_kwargs_for_explicit_true_includes_follow_up_key() -> None:
    """Explicit ``with_follow_up=True`` includes the key with value True."""
    bag: dict[str, Any] = build_chat_kwargs_for(
        config=_fake_config(),
        sensitive_config=_fake_sensitive_config(),
        with_follow_up=True,
    )
    assert bag["with_follow_up"] is True
