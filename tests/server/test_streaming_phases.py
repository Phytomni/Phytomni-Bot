# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the streaming phase-map."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.mcp.streaming_phases import phase_for

pytestmark = pytest.mark.server


def test_knowledge_nodes_map_to_phases() -> None:
    """Knowledge reduce/post nodes map to retrieving/generating phases."""
    assert phase_for("KnowledgeAgent", "retrieve_node") == "retrieving"
    assert phase_for("KnowledgeAgent", "generate_post_node") == "generating"


def test_review_reduce_nodes_map_and_workers_are_dropped() -> None:
    """Review reduce nodes map to phases; worker/dispatch nodes drop."""
    assert phase_for("ReviewAgent", "retrieve_reduce_node") == "retrieving"
    assert phase_for("ReviewAgent", "draft_reduce_node") == "drafting"
    # worker / dispatch nodes are off-whitelist → None (folded away)
    assert phase_for("ReviewAgent", "retrieve_worker_node") is None
    assert phase_for("ReviewAgent", "draft_dispatch") is None


def test_unknown_agent_or_node_returns_none() -> None:
    """Unknown agent or off-whitelist node returns None."""
    assert phase_for("ChatAgent", "whatever") is None
    assert phase_for("KnowledgeAgent", "not_a_node") is None
