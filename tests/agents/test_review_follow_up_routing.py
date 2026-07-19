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

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.graphs.chat_adapters import build_chat_kwargs_for
from tests.support.config_fakes import fake_chat_config, fake_sensitive_config
from tests.support.review_fan_out import (
    assert_send_common,
    build_review_agent,
    draft_route_state,
    follow_up_state,
    plan_query_state,
    review_results_route_state,
    review_summary_state,
)

pytestmark = pytest.mark.agent


# ---------------------------------------------------------------------------
# Per-site prep-node parameter rows.
# ---------------------------------------------------------------------------

_PREP_ROWS = (
    ("plan-query", "plan_query_prep_node", plan_query_state, False),
    ("summary", "summary_prep_node", review_summary_state, False),
    ("follow-up", "follow_up_prep_node", follow_up_state, True),
)
_PREP_PARAMS = tuple(pytest.param(*row, id=row[0]) for row in _PREP_ROWS)


def test_review_chat_prep_rows_are_unique_and_complete() -> None:
    """Keep one explicit parameter row for each Review chat call site."""
    assert [row[0] for row in _PREP_ROWS] == [
        "plan-query",
        "summary",
        "follow-up",
    ]
    assert len({row[0] for row in _PREP_ROWS}) == 3
    assert [row[1] for row in _PREP_ROWS] == [
        "plan_query_prep_node",
        "summary_prep_node",
        "follow_up_prep_node",
    ]


@pytest.mark.parametrize(
    ("_case_id", "node_name", "state_factory", "expected"),
    _PREP_PARAMS,
)
async def test_review_chat_prep_follow_up_contract(
    _case_id: str,
    node_name: str,
    state_factory: Callable[[], dict[str, Any]],
    expected: bool,
) -> None:
    """Each named Review chat prep row preserves its follow-up flag."""
    agent = build_review_agent()
    state = cast(DeepResearchState, state_factory())
    result = await getattr(agent, node_name)(state)
    chat_kwargs = result["chat_payload"]["chat_kwargs"]
    assert chat_kwargs["with_follow_up"] is expected


# ---------------------------------------------------------------------------
# Per-Send-payload route-fan-out assertions.
# ---------------------------------------------------------------------------


def test_route_draft_tasks_payload_disables_follow_up() -> None:
    """Every ``route_draft_tasks`` Send carries ``with_follow_up`` False."""
    agent = build_review_agent()
    state = cast(DeepResearchState, draft_route_state())
    sends = agent.route_draft_tasks(state)

    assert_send_common(sends, node="draft_worker_node")
    for send in sends:
        chat_kwargs = send.arg["chat_payload"]["chat_kwargs"]
        assert chat_kwargs["with_follow_up"] is False


def test_route_review_results_tasks_payload_disables_follow_up() -> None:
    """``route_review_results_tasks`` Sends carry ``with_follow_up`` False."""
    agent = build_review_agent()
    state = cast(DeepResearchState, review_results_route_state())
    sends = agent.route_review_results_tasks(state)

    assert_send_common(sends, node="review_results_worker_node")
    for send in sends:
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
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
    )
    assert "with_follow_up" not in bag


def test_build_chat_kwargs_for_explicit_false_includes_follow_up_key() -> None:
    """Explicit ``with_follow_up=False`` includes the key with value False."""
    bag: dict[str, Any] = build_chat_kwargs_for(
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
        with_follow_up=False,
    )
    assert bag["with_follow_up"] is False


def test_build_chat_kwargs_for_explicit_true_includes_follow_up_key() -> None:
    """Explicit ``with_follow_up=True`` includes the key with value True."""
    bag: dict[str, Any] = build_chat_kwargs_for(
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
        with_follow_up=True,
    )
    assert bag["with_follow_up"] is True
