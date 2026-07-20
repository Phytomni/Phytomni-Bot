# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for neutral Knowledge subgraph typing and mounting helpers."""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.shared.knowledge_subgraph import (
    KnowledgeApp,
    mount_knowledge_node,
)
from tests.support.subgraph_fakes import RecordingKnowledgeApp

pytestmark = pytest.mark.agent


class _RecordingWorkflow:
    """Minimal workflow double that records registered node callables."""

    def __init__(self) -> None:
        self.nodes: dict[str, Any] = {}

    def add_node(self, name: str, node: Any) -> None:
        """Record one LangGraph node registration."""
        self.nodes[name] = node

    def get_node(self, name: str) -> Any:
        """Return one registered node for invocation in the test."""
        return self.nodes[name]


async def test_mount_knowledge_node_registers_and_projects_response() -> None:
    """The neutral mount keeps the compiled app visible to the wrapper."""
    _ = KnowledgeApp
    fake = RecordingKnowledgeApp(
        output={"retrieved_docs": [{"title": "A"}]},
    )
    workflow = _RecordingWorkflow()

    mount_knowledge_node(
        cast(Any, workflow),
        knowledge_app=cast(Any, fake.compiled),
    )

    assert set(workflow.nodes) == {"knowledge"}
    result = await workflow.get_node("knowledge")(
        {"knowledge_payload": {"user_query": "query"}},
    )
    assert result == {
        "knowledge_response": {
            "user_query": "query",
            "retrieved_docs": [{"title": "A"}],
        }
    }
    assert fake.calls == [{"user_query": "query"}]
