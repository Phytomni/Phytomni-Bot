# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Smoke tests for ``agents/shared/knowledge_subgraph.py``.

Covers the wrapper factory ``make_knowledge_node_wrapper`` and the
router ``make_knowledge_after_router``. The wrapper's closure-capture
of the compiled subgraph is the load-bearing xray contract proven by
``/tmp/step62_probe/probe.py``; the smoke tests here lock the
projection + routing behavior without instantiating a real
KnowledgeAgent.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.shared.knowledge_subgraph import (
    make_knowledge_after_router,
    make_knowledge_node_wrapper,
)

pytestmark = pytest.mark.agent


def _fake_knowledge_app(response: dict[str, Any]) -> SimpleNamespace:
    """Build a callable KA seam with explicit, inspectable call state."""
    calls: list[dict[str, Any]] = []

    async def ainvoke(payload: dict[str, Any]) -> dict[str, Any]:
        """Record the call and return the canned response."""
        calls.append(payload)
        return response

    return SimpleNamespace(ainvoke=ainvoke, calls=calls)


@pytest.mark.asyncio
async def test_node_wrapper_projects_state_and_stores_response():
    """Wrapper seeds the unused answer output before invoking retrieve-only."""
    fake_app = _fake_knowledge_app(
        response={"retrieved_docs": [{"id": "doc-1"}]}
    )

    node = make_knowledge_node_wrapper(
        knowledge_app=cast(CompiledStateGraph, fake_app),
        build_input_fn=lambda state: {"user_query": state["query"]},
        extract_output_fn=lambda out: out["retrieved_docs"],
        response_key="docs",
    )

    delta = await node({"query": "what is photosynthesis"})

    assert delta == {"docs": [{"id": "doc-1"}]}
    assert fake_app.calls == [
        {
            "user_query": "what is photosynthesis",
            "final_response": {},
        }
    ]


@pytest.mark.asyncio
async def test_node_wrapper_closure_captures_app_for_xray():
    """``knowledge_app`` is in the wrapper's closure (free var).

    Locks the load-bearing xray contract: ``find_subgraph_pregel``
    walks the wrapper's ``__closure__`` to discover the compiled
    subgraph. If a future refactor passes the app via globals or
    inline lookup, this assertion fails and surfaces the regression.
    """
    fake_app = _fake_knowledge_app(response={"retrieved_docs": []})

    node = make_knowledge_node_wrapper(
        knowledge_app=cast(CompiledStateGraph, fake_app),
        build_input_fn=lambda s: {},
        extract_output_fn=lambda o: o,
        response_key="docs",
    )

    closed_apps = [cell.cell_contents for cell in node.__closure__ or ()]

    assert fake_app in closed_apps


def test_after_router_branches_on_pending_post_knowledge():
    """Router reads ``pending_post_knowledge`` and returns its value."""
    router = make_knowledge_after_router()

    assert router({"pending_post_knowledge": "post_node"}) == "post_node"


def test_after_router_uses_default_when_key_missing():
    """Missing or empty key falls back to ``default`` when set."""
    router = make_knowledge_after_router(default="fallback_node")

    assert router({}) == "fallback_node"
    assert router({"pending_post_knowledge": ""}) == "fallback_node"


def test_after_router_raises_when_no_branch():
    """Missing key with no default surfaces a ValueError, not silent route."""
    router = make_knowledge_after_router()

    with pytest.raises(ValueError, match="knowledge after-router"):
        router({})


def test_after_router_supports_custom_pending_key():
    """Custom ``pending_post_key`` for graphs with non-default routing."""
    router = make_knowledge_after_router(pending_post_key="next_step")

    assert router({"next_step": "node_x"}) == "node_x"


def test_after_router_ignores_non_string_pending():
    """Non-string pending value triggers default / raise path."""
    router = make_knowledge_after_router(default="d")

    assert router({"pending_post_knowledge": 42}) == "d"
    assert router({"pending_post_knowledge": None}) == "d"


@pytest.mark.asyncio
async def test_node_wrapper_returns_only_response_key():
    """Delta dict contains exactly one key — the configured response_key."""
    fake_app = _fake_knowledge_app(
        response={"retrieved_docs": [], "extra_field": "ignored"}
    )

    node = make_knowledge_node_wrapper(
        knowledge_app=cast(CompiledStateGraph, fake_app),
        build_input_fn=lambda s: {"user_query": "q"},
        extract_output_fn=lambda out: out["retrieved_docs"],
        response_key="docs",
    )

    delta = await node({})

    assert set(delta.keys()) == {"docs"}
    assert delta["docs"] == []


@pytest.mark.asyncio
async def test_node_wrapper_propagates_input_fn_exceptions():
    """``build_input_fn`` exceptions surface; the wrapper does not swallow."""
    fake_app = _fake_knowledge_app(response={})

    def _boom(state: Any) -> dict[str, Any]:
        raise KeyError("missing field")

    node = make_knowledge_node_wrapper(
        knowledge_app=cast(CompiledStateGraph, fake_app),
        build_input_fn=_boom,
        extract_output_fn=lambda o: o,
        response_key="docs",
    )

    with pytest.raises(KeyError, match="missing field"):
        await node(SimpleNamespace())
    assert not fake_app.calls
