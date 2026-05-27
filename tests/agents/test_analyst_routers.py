# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Router-method tests for analyst/core.py.

Pin the six ``route_after_*`` methods that drive ``AnalystAgent``'s
conditional edges. None of them read ``self``, so each call bypasses
the heavyweight ``AnalystAgent.__init__`` graph build by invoking the
method through the class with a sentinel ``self`` placeholder.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.analyst.core import AnalystAgent

pytestmark = pytest.mark.unit

# A throwaway placeholder for ``self``; the routers ignore it. Cast to
# ``Any`` so pyright accepts the unbound-method call without inventing
# an AnalystAgent instance (which would compile the full graph).
_SELF: Any = object()


def _state(**fields: Any) -> Any:
    """Return an ``AnalystAgentsState``-shaped dict with the given keys.

    Cast to ``Any`` because the TypedDict requires every key; the
    routers only consult two or three keys at a time so an
    intentionally-partial dict is the correct shape for these tests.
    """
    return cast(Any, fields)


def test_route_after_extract_auto_select_wins_over_preset_plan() -> None:
    """``is_auto_select`` short-circuits to ``data_select_node`` first."""
    state = _state(is_auto_select=True, is_preset_plan=True)

    assert AnalystAgent.route_after_extract(_SELF, state) == (
        "data_select_node"
    )


def test_route_after_extract_preset_plan_skips_to_tool_extract() -> None:
    """No auto-select but a preset plan jumps past the planner entirely."""
    state = _state(is_auto_select=False, is_preset_plan=True)

    assert AnalystAgent.route_after_extract(_SELF, state) == (
        "tool_extract_node"
    )


def test_route_after_extract_defaults_to_method_retrieve() -> None:
    """The unmarked path falls through to ``method_retrieve_node``."""
    state = _state(is_auto_select=False, is_preset_plan=False)

    assert AnalystAgent.route_after_extract(_SELF, state) == (
        "method_retrieve_node"
    )


def test_route_after_data_select_branches_on_preset_plan() -> None:
    """Same preset-plan short-circuit as ``route_after_extract``."""
    preset = _state(is_preset_plan=True)
    no_preset = _state(is_preset_plan=False)

    assert AnalystAgent.route_after_data_select(_SELF, preset) == (
        "tool_extract_node"
    )
    assert AnalystAgent.route_after_data_select(_SELF, no_preset) == (
        "method_retrieve_node"
    )


def test_route_after_plan_branches_on_preset_plan() -> None:
    """Preset plans skip the critic ``check_node``."""
    preset = _state(is_preset_plan=True)
    no_preset = _state(is_preset_plan=False)

    assert AnalystAgent.route_after_plan(_SELF, preset) == "tool_extract_node"
    assert AnalystAgent.route_after_plan(_SELF, no_preset) == "check_node"


def test_route_after_check_approved_or_preset_plan_skips_replan() -> None:
    """``APPROVED`` feedback OR ``is_preset_plan`` exits the plan loop."""
    approved = _state(plan_feedback="APPROVED", is_preset_plan=False)
    preset = _state(plan_feedback="REJECTED", is_preset_plan=True)
    rejected = _state(plan_feedback="REJECTED", is_preset_plan=False)

    assert AnalystAgent.route_after_check(_SELF, approved) == (
        "tool_extract_node"
    )
    assert AnalystAgent.route_after_check(_SELF, preset) == (
        "tool_extract_node"
    )
    assert AnalystAgent.route_after_check(_SELF, rejected) == "plan_node"


def test_route_after_submit_polls_only_when_polling_flag_set() -> None:
    """``is_polling`` decides whether the agent stays in the polling loop."""
    polling = _state(is_polling=True)
    fire_and_forget = _state(is_polling=False)

    assert AnalystAgent.route_after_submit(_SELF, polling) == "pooling_node"
    assert AnalystAgent.route_after_submit(_SELF, fire_and_forget) == "__end__"


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_route_after_pooling_terminal_statuses_exit(status: str) -> None:
    """Each terminal status ends the polling loop."""
    state = _state(task_status=status)

    assert AnalystAgent.route_after_pooling(_SELF, state) == "__end__"


def test_route_after_pooling_running_status_keeps_polling() -> None:
    """Any non-terminal status keeps the agent in the polling loop."""
    state = _state(task_status="RUNNING")

    assert AnalystAgent.route_after_pooling(_SELF, state) == "pooling_node"
