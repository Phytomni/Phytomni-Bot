# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for shared LangGraph runtime helpers."""

from pathlib import Path

from pydantic import SecretStr

from mcp_server_phytomni.langgraph_runner import (
    GraphRegistry,
    ainvoke_graph,
    build_runnable_config,
    config_fingerprint,
    ensure_checkpointer,
    ensure_thread_id,
)

PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src/mcp_server_phytomni"


class FakeGraph:
    """Small async graph stand-in used to inspect runner behavior."""

    def __init__(self):
        """Verify init  ."""
        self.initial_state = None
        self.config = None

    async def ainvoke(self, initial_state, config=None):
        """Verify ainvoke."""
        self.initial_state = initial_state
        self.config = config
        return {"final": initial_state["value"]}

    def snapshot(self):
        """Return captured invocation details."""
        return {"initial_state": self.initial_state, "config": self.config}


def test_ensure_thread_id_keeps_existing_value():
    """Verify ensure thread id keeps existing value."""
    assert ensure_thread_id("thread-1") == "thread-1"


def test_build_runnable_config_uses_thread_id():
    """Verify build runnable config uses thread id."""
    assert build_runnable_config("thread-1") == {
        "configurable": {"thread_id": "thread-1"}
    }


def test_ensure_thread_id_generates_value_when_missing():
    """Verify ensure thread id generates value when missing."""
    first = ensure_thread_id()
    second = ensure_thread_id("")

    assert first
    assert second
    assert first != second


def test_ensure_checkpointer_creates_fresh_instances():
    """Verify ensure checkpointer creates fresh instances."""
    first = ensure_checkpointer()
    second = ensure_checkpointer()

    assert first is not second
    assert ensure_checkpointer(first) is first


async def test_ainvoke_graph_passes_standard_config():
    """Verify ainvoke graph passes standard config."""
    graph = FakeGraph()

    result = await ainvoke_graph(graph, {"value": "ok"}, thread_id="thread-1")

    assert result == {"final": "ok"}
    assert graph.initial_state == {"value": "ok"}
    assert graph.config == {"configurable": {"thread_id": "thread-1"}}


def test_config_fingerprint_is_stable_and_omits_secret_fields():
    """Verify config fingerprint is stable and omits secret fields."""
    left = config_fingerprint(
        {
            "model": "demo",
            "api_key": "plain-secret",
            "nested": {"password": "hidden", "temperature": 0.2},
            "secret_value": SecretStr("also-hidden"),
        }
    )
    right = config_fingerprint(
        {
            "secret_value": SecretStr("changed"),
            "nested": {"temperature": 0.2, "password": "changed"},
            "api_key": "changed",
            "model": "demo",
        }
    )

    assert left == right
    assert "plain-secret" not in left
    assert "also-hidden" not in left
    assert "password" not in left


def test_graph_registry_reuses_by_name_and_fingerprint():
    """Verify graph registry reuses by name and fingerprint."""
    registry: GraphRegistry[object] = GraphRegistry()
    created = 0

    def factory():
        """Verify factory."""
        nonlocal created
        created += 1
        return object()

    first = registry.get_or_create("data", factory, {"model": "demo"})
    second = registry.get_or_create("data", factory, {"model": "demo"})
    third = registry.get_or_create("knowledge", factory, {"model": "demo"})

    assert first is second
    assert first is not third
    assert created == 2
    assert registry.count() == 2


def test_graph_registry_can_clear_one_name_or_all():
    """Verify graph registry can clear one name or all."""
    registry: GraphRegistry[object] = GraphRegistry()
    registry.get_or_create("data", object, {"model": "demo"})
    registry.get_or_create("knowledge", object, {"model": "demo"})

    registry.clear("data")

    assert registry.count() == 1

    registry.clear()

    assert registry.count() == 0


def test_agent_sources_use_shared_runner_for_graph_invocation():
    """Verify agent sources use shared runner for graph invocation."""
    agent_files = sorted(PACKAGE_DIR.glob("*_agents.py"))
    source_by_name = {
        agent_file.name: agent_file.read_text(encoding="utf-8")
        for agent_file in agent_files
    }

    assert source_by_name
    for source in source_by_name.values():
        assert "checkpointer=MemorySaver()" not in source
        assert "self.app.ainvoke(" not in source
        assert (
            'config = {"configurable": {"thread_id": thread_id}}' not in source
        )
