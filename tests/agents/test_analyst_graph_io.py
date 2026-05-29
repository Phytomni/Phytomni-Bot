# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IO contract + 3-schema compilation tests for AnalystAgent.

Pins the public ``AnalystInput`` / ``AnalystOutput`` shape, the
binary-compatible ``AnalystAgentsState`` alias, and the five
``add_conditional_edges`` sources LangGraph records on the compiled
builder. Conditional-routing behaviour itself lives in
``test_analyst_routers.py`` to avoid duplicating the existing
coverage there.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.state import (
    AnalystAgentsState,
    AnalystInput,
    AnalystOutput,
    AnalystState,
)

pytestmark = pytest.mark.agent


def _required_keys(td: type) -> set[str]:
    return set(getattr(td, "__required_keys__", set()))


def _optional_keys(td: type) -> set[str]:
    return set(getattr(td, "__optional_keys__", set()))


def test_analyst_input_requires_only_query() -> None:
    """``AnalystInput`` requires exactly ``query``.

    Parent graphs mounting analyst as a subgraph owe exactly one
    field; the rest default through ``arun`` or the analyst config.
    """
    assert _required_keys(AnalystInput) == {"query"}


def test_analyst_input_optional_keys_match_arun_kwargs() -> None:
    """Optional ``AnalystInput`` keys mirror ``arun`` kwargs.

    The nine optional fields are the kwargs ``arun`` already accepts
    (``goal_description`` / ``preset_plan`` / ``data_list`` /
    ``obs_file_list`` / ``compute_resource`` / ``output_dir`` plus
    the three boolean toggles). A future ``arun`` kwarg surfaces
    here before parent graphs can compose against it.
    """
    assert _optional_keys(AnalystInput) == {
        "goal_description",
        "preset_plan",
        "data_list",
        "obs_file_list",
        "compute_resource",
        "output_dir",
        "is_polling",
        "is_auto_select",
        "is_preset_plan",
    }


def test_analyst_output_carries_surface_keys_and_plan_contract() -> None:
    """``AnalystOutput`` covers ``surface_keys`` + plan-style fields.

    The four top-level ``surface_keys`` ``arun`` returns through
    ``merge_intermediate_state`` (``task_id`` / ``output_dir`` /
    ``job_name`` / ``compute_resource``) must remain on the output
    so the task-style trim still works; the plan / tool_usages /
    task_status fields are what downstream adapters consume; and
    the observability intermediates feed ``phytomni_state``.
    """
    assert set(get_type_hints(AnalystOutput).keys()) == {
        "task_id",
        "output_dir",
        "job_name",
        "compute_resource",
        "plan",
        "tool_usages",
        "task_status",
        "goal_description",
        "method_context",
        "plan_feedback",
        "plan_retries",
        "extracted_tools",
        "error_detail",
    }


def test_analyst_state_carries_full_field_union() -> None:
    """``AnalystState`` retains every legacy ``AnalystAgentsState`` key.

    Binary compatibility for internal node annotations that still
    type-hint ``AnalystAgentsState`` (alias of ``AnalystState``).
    The ``error_detail`` field is additive and surfaces only on the
    ``failure_state`` path.
    """
    expected = {
        "query",
        "goal_description",
        "obs_file_list",
        "data_list",
        "output_dir",
        "compute_resource",
        "job_name",
        "method_context",
        "preset_plan",
        "plan",
        "plan_feedback",
        "plan_retries",
        "extracted_tools",
        "tool_usages",
        "task_id",
        "task_status",
        "is_polling",
        "is_auto_select",
        "is_preset_plan",
        "error_detail",
    }
    assert set(get_type_hints(AnalystState).keys()) == expected


def test_analyst_agents_state_is_analyst_state_alias() -> None:
    """Legacy ``AnalystAgentsState`` is an alias of ``AnalystState``.

    Internal node signatures across ``core.py`` / ``graph.py`` /
    ``planning.py`` / ``submission.py`` continue to annotate with
    ``AnalystAgentsState`` and must resolve to the new
    ``AnalystState``.
    """
    assert AnalystAgentsState is AnalystState


def test_analyst_subgraph_exposes_five_conditional_sources() -> None:
    """Compiled analyst graph records all five conditional routers.

    Pins the post-IO-schema topology: ``parse_query_node``,
    ``data_select_node``, ``check_node``, ``submit_node``, and
    ``pooling_node`` each register an ``add_conditional_edges``
    branch. Reaching this assertion also proves the 3-schema
    ``StateGraph`` form compiled — a mismatched ``input_schema`` /
    ``output_schema`` would raise during ``AnalystAgent()`` before
    ``branches`` could be inspected. A future refactor that
    collapses one of these branches surfaces here before manifest
    export.
    """
    agent = AnalystAgent()
    branches = set(agent.app.builder.branches.keys())
    assert branches == {
        "parse_query_node",
        "data_select_node",
        "check_node",
        "submit_node",
        "pooling_node",
    }
