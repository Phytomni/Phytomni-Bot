# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline spike exercising LangGraph nested-checkpoint semantics.

Pins the conclusion behind plan ledger A3 / W4: a child compiled
subgraph mounted via ``parent.add_node(name, child_app)`` inherits
the parent's ``thread_id`` through ``configurable`` and the parent's
``MemorySaver`` captures the child's intermediate state in one
checkpoint stream, so adapter-bridged subgraphs do not need a
separate checkpoint strategy.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

pytestmark = pytest.mark.agent


class _ChildState(TypedDict):
    counter: int
    text: str


class _ParentState(TypedDict):
    counter: int
    text: str


def _child_node(state: _ChildState) -> dict:
    """Add 1 to the running counter and tag the text with ``child``."""
    return {
        "counter": state.get("counter", 0) + 1,
        "text": "child-touched",
    }


def _parent_root_node(state: _ParentState) -> dict:
    """Add 10 to the running counter and tag the text with ``parent``."""
    return {
        "counter": state.get("counter", 0) + 10,
        "text": "parent-touched",
    }


def _build_child_app(checkpointer: MemorySaver):
    """Return a single-node child app compiled with its own checkpointer."""
    workflow = StateGraph(_ChildState)
    workflow.add_node("child_node", _child_node)
    workflow.add_edge(START, "child_node")
    workflow.add_edge("child_node", END)
    return workflow.compile(checkpointer=checkpointer)


def _build_parent_with_mounted_child(parent_cp: MemorySaver, child_app):
    """Return a parent app embedding ``child_app`` via ``add_node``."""
    workflow = StateGraph(_ParentState)
    workflow.add_node("parent_root", _parent_root_node)
    workflow.add_node("embedded_child", child_app)
    workflow.add_edge(START, "parent_root")
    workflow.add_edge("parent_root", "embedded_child")
    workflow.add_edge("embedded_child", END)
    return workflow.compile(checkpointer=parent_cp)


@pytest.mark.asyncio
async def test_parent_and_subgraph_share_thread_id_state() -> None:
    """Parent and embedded child agree on the final counter and text.

    Parent root adds 10 to the seed counter, then the embedded
    child adds 1; the parent's final state reads back the child's
    updates because LangGraph projects shared keys (``counter`` /
    ``text``) across the parent / child boundary automatically.
    """
    child_cp = MemorySaver()
    parent_cp = MemorySaver()
    child_app = _build_child_app(child_cp)
    parent_app = _build_parent_with_mounted_child(parent_cp, child_app)

    config: RunnableConfig = {"configurable": {"thread_id": "spike-thread-1"}}
    result = await parent_app.ainvoke(
        {"counter": 0, "text": "seed"},
        config=config,
    )

    assert result["counter"] == 11
    assert result["text"] == "child-touched"


@pytest.mark.asyncio
async def test_parent_checkpoint_captures_nested_child_state() -> None:
    """Parent's MemorySaver records a checkpoint after the child runs.

    Pins the persistence contract: even though the child compiles
    with its own checkpointer, the parent's checkpointer still
    stores at least one tuple keyed by the parent's ``thread_id``,
    so resumability and ``aget_state`` introspection drive off the
    parent's saver alone.
    """
    child_cp = MemorySaver()
    parent_cp = MemorySaver()
    child_app = _build_child_app(child_cp)
    parent_app = _build_parent_with_mounted_child(parent_cp, child_app)

    config: RunnableConfig = {"configurable": {"thread_id": "spike-thread-2"}}
    await parent_app.ainvoke(
        {"counter": 0, "text": "seed"},
        config=config,
    )

    checkpoint_tuple = await parent_cp.aget_tuple(config)
    assert checkpoint_tuple is not None
    assert checkpoint_tuple.checkpoint["channel_values"]["counter"] == 11


@pytest.mark.asyncio
async def test_parent_resume_with_same_thread_id_returns_prior_state() -> None:
    """A second ``aget_state`` call under the same thread_id sees stored state.

    Pins the resumability shape: a follow-up reader pointed at the
    same ``thread_id`` reads back the final state from the parent's
    checkpointer without re-invoking the graph, so the typical
    ``app.ainvoke`` + ``app.aget_state`` introspection seam works
    transparently when a child subgraph is mounted.
    """
    child_cp = MemorySaver()
    parent_cp = MemorySaver()
    child_app = _build_child_app(child_cp)
    parent_app = _build_parent_with_mounted_child(parent_cp, child_app)

    config: RunnableConfig = {"configurable": {"thread_id": "spike-thread-3"}}
    await parent_app.ainvoke(
        {"counter": 5, "text": "seed"},
        config=config,
    )
    snapshot = await parent_app.aget_state(config)
    assert snapshot.values["counter"] == 16
    assert snapshot.values["text"] == "child-touched"
