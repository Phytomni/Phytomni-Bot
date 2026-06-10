# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public IO schemas for the AnalystAgent subgraph.

``AnalystInput`` is the narrow request shape a parent graph supplies;
``AnalystOutput`` carries the task-style ``surface_keys`` plus the
plan, tool usages, status, and per-node observability intermediates;
``AnalystState`` is the full working dict and stays binary-compatible
with the legacy ``AnalystAgentsState`` alias.
"""

# NOTE: ``from __future__ import annotations`` is deliberately
# omitted. PEP 705 ``Required[]`` markers are erased by lazy
# annotations and ``TypedDict.__required_keys__`` is computed at
# class-definition time, so future annotations would silently drop
# every ``Required[]`` marker on ``AnalystInput``.

from typing import Any, Dict, List, Optional, Required, TypedDict


class AnalystInput(TypedDict, total=False):
    """Request fields a parent graph supplies when mounting analyst.

    ``query`` is the only Required key so a parent owes the subgraph
    one field; remaining keys default in ``arun`` when absent. The
    booleans (``is_polling`` / ``is_auto_select`` / ``is_preset_plan``)
    flip between the polling, auto-select, and preset-plan paths.
    """

    query: Required[str]
    goal_description: str
    preset_plan: str
    data_list: Dict[str, str]
    obs_file_list: List
    compute_resource: str
    output_dir: str
    is_polling: bool
    is_auto_select: bool
    is_preset_plan: bool


class AnalystOutput(TypedDict):
    """Output surface exposed to the parent graph after compile.

    The four ``surface_keys`` (``task_id`` / ``output_dir`` /
    ``job_name`` / ``compute_resource``) drive the top-level result
    that ``arun`` returns through
    ``merge_intermediate_state(surface_keys=...)``. The remaining
    fields stay on the final state so downstream consumers and the
    HTTP/MCP ``raw.phytomni_state`` block see the executed plan,
    extracted tools, plan critique, and failure detail. Field types
    mirror the existing ``AnalystAgentsState`` so internal callers
    continue to bracket-access without Optional widening.
    """

    # surface_keys (top-level result for arun callers)
    task_id: str
    output_dir: str
    job_name: str
    compute_resource: str
    # subgraph-composition contract (downstream adapters consume these)
    plan: str
    tool_usages: str
    task_status: str
    # observability intermediates (lifted into phytomni_state)
    goal_description: str
    method_context: Dict[str, str]
    plan_feedback: Optional[str]
    plan_retries: int
    extracted_tools: List
    # failure detail (set by arun's failure_state on graph errors)
    error_detail: str


class AnalystState(TypedDict):
    """Full working state for the AnalystAgent LangGraph workflow.

    Mirrors the legacy ``AnalystAgentsState`` field set plus six
    optional keys surrounding the prep+post subgraph splits — three
    for the chat subgraph (``pending_post`` / ``chat_payload`` /
    ``chat_response``) for the chat subgraph split, and
    three for the knowledge subgraph (``pending_post_knowledge`` /
    ``knowledge_payload`` / ``knowledge_response``) for the knowledge
    subgraph split — along with the additive
    ``error_detail`` key that ``failure_state`` writes when the graph
    raises. Value types intentionally match the legacy annotations so
    internal node bracket access
    (``state["method_context"]["upload_context"]`` etc.) continues to
    satisfy mypy without Optional widening.
    """

    query: str
    goal_description: str
    obs_file_list: List
    data_list: Dict[str, str]
    output_dir: str
    compute_resource: str
    job_name: str
    method_context: Dict[str, str]
    preset_plan: str
    plan: str
    plan_feedback: Optional[str]
    plan_retries: int
    extracted_tools: List
    tool_usages: str
    task_id: str
    task_status: str
    is_polling: bool
    is_auto_select: bool
    is_preset_plan: bool
    error_detail: str
    pending_post: Optional[str]
    chat_payload: Optional[Dict[str, Any]]
    chat_response: Optional[Dict[str, Any]]
    pending_post_knowledge: Optional[str]
    knowledge_payload: Optional[Dict[str, Any]]
    knowledge_response: Optional[Dict[str, Any]]


AnalystAgentsState = AnalystState
