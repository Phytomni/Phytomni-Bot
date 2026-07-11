# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for the supported LangGraph public API window."""

from __future__ import annotations

from importlib.metadata import version
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

pytestmark = pytest.mark.unit


class _State(TypedDict):
    """Minimal state used to compile and invoke a contract graph."""

    count: int


class _Context(TypedDict):
    """Minimal typed runtime context used by a graph node."""

    request_id: str


def _increment(state: _State, runtime: Runtime[_Context]) -> _State:
    """Increment state after observing context and the injected store."""
    assert runtime.context["request_id"] == "request-contract"
    assert runtime.store is not None
    return {"count": state["count"] + 1}


def test_langgraph_runtime_window_is_minor_1_2() -> None:
    """The runtime stays inside the declared 1.2 compatibility line."""
    major, minor, *_ = version("langgraph").split(".")
    assert (int(major), int(minor)) == (1, 2)


def test_state_graph_supports_runtime_checkpointer_and_store() -> None:
    """The public graph APIs used by Phytomni compile and invoke together."""
    graph = StateGraph(_State, context_schema=_Context)
    graph.add_node("increment", _increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    app = graph.compile(
        checkpointer=MemorySaver(),
        store=InMemoryStore(),
    )

    result = app.invoke(
        {"count": 1},
        config={"configurable": {"thread_id": "thread-contract"}},
        context={"request_id": "request-contract"},
    )

    assert result == {"count": 2}


def test_stream_and_interrupt_primitives_remain_public() -> None:
    """Streaming and resume primitives retain their supported public shape."""
    with pytest.raises(RuntimeError):
        get_stream_writer()

    assert callable(interrupt)
    assert Command(resume={"approved": True}).resume == {"approved": True}
