# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the shared chat subgraph wrapper helper.

Pins the contract of the factory-built async node body and the
pending-post conditional-edges router, plus the structural xray
guarantee: ``CHAT_APP`` must be reachable from the wrapper's
closure so ``find_subgraph_pregel`` expands the chat block inside
the consumer's mermaid render.
"""

from __future__ import annotations

from typing import Any, TypedDict
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph

from mcp_server_phytomni.agents.shared.chat_subgraph import (
    make_chat_after_router,
    make_chat_node_wrapper,
    mount_chat_node,
)

pytestmark = pytest.mark.agent


class _ConsumerState(TypedDict, total=False):
    """Minimal consumer state for the structural xray test."""

    user_query: str
    phyto_response: dict[str, Any]


async def test_make_chat_node_wrapper_invokes_chat_app_via_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper threads state through build -> CHAT_APP -> extract."""
    captured: dict[str, Any] = {}

    def build_input(state: dict) -> dict:
        captured["build_state"] = state
        return {"user_query": state["user_query"]}

    def extract_output(chat_output: dict) -> dict:
        captured["extract_arg"] = chat_output
        return chat_output["response"]

    fake_response = {"choices": [{"message": {"content": "hi"}}]}
    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(return_value={"response": fake_response})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.chat_subgraph.CHAT_APP",
        fake_app,
    )

    wrapper = make_chat_node_wrapper(
        build_input_fn=build_input,
        extract_output_fn=extract_output,
        response_key="phyto_response",
    )

    result = await wrapper({"user_query": "What is X?"})

    assert captured["build_state"] == {"user_query": "What is X?"}
    fake_app.ainvoke.assert_awaited_once_with({"user_query": "What is X?"})
    assert captured["extract_arg"] == {"response": fake_response}
    assert result == {"phyto_response": fake_response}


async def test_make_chat_node_wrapper_state_delta_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returned dict carries only the configured response_key."""
    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(return_value={"response": {"ok": True}})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.chat_subgraph.CHAT_APP",
        fake_app,
    )

    wrapper = make_chat_node_wrapper(
        build_input_fn=lambda s: {"user_query": "q"},
        extract_output_fn=lambda out: out["response"],
        response_key="custom_key",
    )

    result = await wrapper({"user_query": "q", "extra": "ignored"})

    assert set(result.keys()) == {"custom_key"}


def test_make_chat_after_router_returns_pending_post() -> None:
    """Router returns the value held under the pending_post key."""
    router = make_chat_after_router(
        pending_post_key="pending_post",
        default="default_post",
    )

    assert router({"pending_post": "follow_up_post"}) == "follow_up_post"


def test_make_chat_after_router_falls_back_to_default() -> None:
    """Missing pending_post collapses to the configured default."""
    router = make_chat_after_router(
        pending_post_key="pending_post",
        default="default_post",
    )

    assert router({}) == "default_post"
    assert router({"pending_post": ""}) == "default_post"


def test_make_chat_after_router_raises_when_no_signal() -> None:
    """Missing pending_post AND no default raises ValueError."""
    router = make_chat_after_router(
        pending_post_key="pending_post",
        default=None,
    )

    with pytest.raises(ValueError):
        router({})


def test_factory_built_wrapper_triggers_xray_expansion() -> None:
    """A node registered via the factory exposes the chat subgraph.

    LangGraph's ``find_subgraph_pregel`` scans node closures for a
    Pregel app at parent ``compile()`` time. The factory binds the
    helper module's module-level ``CHAT_APP`` into the wrapper's
    closure, so an xray render of the parent graph expands the chat
    subgraph's internal nodes under a ``chat:*`` prefix.
    """

    async def prep_node(state: _ConsumerState) -> dict[str, str]:
        return {"user_query": state.get("user_query") or "Q"}

    workflow = StateGraph(state_schema=_ConsumerState)
    workflow.add_node("prep", prep_node)
    mount_chat_node(
        workflow,
        build_input_fn=lambda s: {"user_query": s.get("user_query", "")},
        extract_output_fn=lambda out: out.get("response") or {},
        response_key="phyto_response",
    )
    workflow.add_edge(START, "prep")
    workflow.add_edge("prep", "chat")
    workflow.add_edge("chat", END)
    compiled = workflow.compile()

    xray_node_keys = list(compiled.get_graph(xray=True).nodes.keys())
    chat_inner_keys = [k for k in xray_node_keys if k.startswith("chat:")]

    assert chat_inner_keys, (
        "xray expansion did not fire; nodes were: " f"{sorted(xray_node_keys)}"
    )
