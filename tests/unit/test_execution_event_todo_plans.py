# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Declarative semantic Todo plan tests."""

from mcp_server_phytomni.mcp.streaming_phases import (
    declared_phases_for,
    todo_snapshot_for_phase,
)
from mcp_server_phytomni.public_agent_catalog import PUBLIC_AGENT_CATALOG


def test_stable_graph_workflow_declares_semantic_plan_and_statuses() -> None:
    """Verify stable graph workflow declares semantic plan and statuses."""
    assert declared_phases_for("KnowledgeAgent") == (
        "retrieving",
        "generating",
    )
    snapshot = todo_snapshot_for_phase("KnowledgeAgent", "generating")
    assert [item["status"] for item in snapshot] == [
        "completed",
        "in_progress",
    ]
    assert [item["label_key"] for item in snapshot] == [
        "chat.execution.todoPhase.retrieving",
        "chat.execution.todoPhase.generating",
    ]


def test_every_public_agent_uses_the_catalog_declared_plan() -> None:
    """Verify every public agent uses the catalog declared plan."""

    for item in PUBLIC_AGENT_CATALOG:
        assert declared_phases_for(item.tool) == item.todo_phases


def test_unknown_agent_has_no_fabricated_todo() -> None:
    """Verify unknown agent has no fabricated todo."""
    assert declared_phases_for("UnknownAgent") == ()
    assert not todo_snapshot_for_phase("UnknownAgent", "thinking")
