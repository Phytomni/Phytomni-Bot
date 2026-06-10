# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``DeepResearchAgent`` per-dimension draft fan-out.

The draft site routes through a Send-dispatch triad
(``draft_dispatch`` → N × ``draft_worker_node`` → ``draft_reduce_node``)
where each worker awaits the module-level ``CHAT_APP`` so xray expands
the chat subgraph under each worker. Also covers partial failure,
reduce ordering, and xray subgraph expansion.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Send

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_AGENT_MODULE = "mcp_server_phytomni.agents.review.agent"


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for the draft fan-out."""
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _ok_chat_response(text: str) -> dict[str, Any]:
    """Build a minimal chat-completion response wrapping ``text``."""
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------------------
# Prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_draft_prepare_tasks_node_returns_empty_delta() -> None:
    """``draft_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "dimension_params": [
                {"subtopic": "s0", "knowledge": "k0"},
            ],
        },
    )
    result = await agent.draft_prepare_tasks_node(state)
    assert result == {}


# ---------------------------------------------------------------------------
# Flag-on route_draft_tasks returns N Send payloads.
# ---------------------------------------------------------------------------


def test_route_draft_tasks_returns_n_sends() -> None:
    """``route_draft_tasks`` returns one Send per dimension_params entry."""
    agent = _build_agent()
    params = [
        {"subtopic": "photosynthesis", "knowledge": "snippet-0"},
        {"subtopic": "chlorophyll", "knowledge": "snippet-1"},
        {"subtopic": "stomatal", "knowledge": "snippet-2"},
    ]
    state = cast(DeepResearchState, {"dimension_params": params})
    sends = agent.route_draft_tasks(state)

    assert len(sends) == 3
    for i, (send, param) in enumerate(zip(sends, params)):
        assert isinstance(send, Send)
        assert send.node == "draft_worker_node"
        assert send.arg["task_index"] == i
        assert send.arg["subtopic"] == param["subtopic"]
        assert send.arg["knowledge"] == param["knowledge"]
        chat_payload = send.arg["chat_payload"]
        # The prompt-builder stitches subtopic/knowledge into the
        # ``user/deep_research_dimension`` template; both strings must
        # appear in the resulting user_query.
        assert param["subtopic"] in chat_payload["user_query"]
        assert param["knowledge"] in chat_payload["user_query"]
        assert "chat_kwargs" in chat_payload
        assert chat_payload["chat_kwargs"]["with_follow_up"] is False


# ---------------------------------------------------------------------------
# Flag-on worker success: writes (task_index, content) tuple.
# ---------------------------------------------------------------------------


async def test_draft_worker_node_success_writes_indexed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``(task_index, content)`` on successful ainvoke."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(
            return_value={"response": _ok_chat_response("draft-A")}
        )
    )
    monkeypatch.setattr(f"{_AGENT_MODULE}.CHAT_APP", fake_app)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 2,
            "subtopic": "auxin signalling",
            "knowledge": "snippet",
            "chat_payload": {
                "user_query": "prompt",
                "chat_kwargs": {},
            },
        },
    )
    result = await agent.draft_worker_node(state)

    assert result["draft_indexed_results"] == [(2, "draft-A")]
    assert "failures" not in result
    assert fake_app.ainvoke.await_count == 1


# ---------------------------------------------------------------------------
# Flag-on worker exception: writes empty sentinel AND FailureRecord.
# ---------------------------------------------------------------------------


async def test_draft_worker_node_exception_writes_sentinel_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``""`` sentinel AND FailureRecord dict on exception."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(side_effect=RuntimeError("chat timeout"))
    )
    monkeypatch.setattr(f"{_AGENT_MODULE}.CHAT_APP", fake_app)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 1,
            "subtopic": "drought tolerance",
            "knowledge": "snippet",
            "chat_payload": {
                "user_query": "prompt",
                "chat_kwargs": {},
            },
        },
    )
    result = await agent.draft_worker_node(state)

    assert result["draft_indexed_results"] == [(1, "")]
    failures = result.get("failures", [])
    assert len(failures) == 1
    rec = failures[0]
    # FailureRecord is a TypedDict — check structural keys, not isinstance.
    assert rec["kind"] == "execute"
    assert "chat timeout" in rec["message"]
    assert rec["task_label"] == "draft:1"
    assert rec["traceback_digest"] is not None


# ---------------------------------------------------------------------------
# Flag-on reduce: sorts indexed_results by task_index before projecting.
# ---------------------------------------------------------------------------


async def test_draft_reduce_node_sorts_by_task_index() -> None:
    """``draft_reduce_node`` sorts results by task_index before projecting.

    Delivers the same ``draft_contents`` ordering regardless of the
    order concurrent workers completed.
    """
    agent = _build_agent()
    # Supply results out-of-order (task 1 arrives before task 0).
    state = cast(
        DeepResearchState,
        {
            "draft_indexed_results": [(2, "C"), (0, "A"), (1, "B")],
        },
    )
    result = await agent.draft_reduce_node(state)

    assert result["draft_contents"] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Flag-on partial failure (1 of N): reduce produces N draft_contents entries.
# ---------------------------------------------------------------------------


async def test_draft_reduce_node_partial_failure_keeps_n_entries() -> None:
    """Reduce yields N entries even when one worker returned the sentinel.

    The failed dimension gets the empty-string sentinel slot; the reduce
    does not skip it, so downstream ``review_node`` still receives a slot
    for every dimension.
    """
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "draft_indexed_results": [
                (0, ""),  # failed
                (1, "draft-B"),
                (2, ""),  # failed
            ],
        },
    )
    result = await agent.draft_reduce_node(state)

    assert result["draft_contents"] == ["", "draft-B", ""]


# ---------------------------------------------------------------------------
# Structural: flag-on graph has Send triad; flag-off keeps legacy.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_has_send_triad() -> None:
    """Flag-on graph has ``draft_dispatch`` and ``draft_reduce_node``.

    ``draft_worker_node`` awaits the module-level ``CHAT_APP`` (a
    ``CompiledStateGraph``), so LangGraph's xray render REPLACES the
    flat key with prefixed children (``draft_worker_node:<child>``).
    A plain ``"draft_worker_node"`` key would indicate xray did NOT
    discover the chat subgraph; the prefixed form is the success
    signal.
    """
    agent = _build_agent()
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "draft_dispatch" in node_keys
    assert any(
        key.startswith("draft_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "draft_reduce_node" in node_keys
    assert "draft_node" not in node_keys


# ---------------------------------------------------------------------------
# Xray: the chat subgraph expands UNDER ``draft_worker_node``.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_xray_expands_chat_subgraph() -> None:
    """Flag-on graph exposes the chat subgraph to ``xray``.

    ``find_subgraph_pregel`` walks the worker's closure-free-variable
    path through ``__globals__`` to find the module-level ``CHAT_APP``
    and inlines it. LangGraph prefixes the inlined subgraph's child
    node keys with the PARENT node's ``add_node()`` name — i.e.
    ``draft_worker_node:<child>``, NOT a literal ``chat:`` namespace
    (the prefix is the parent node name, not the subgraph's own name).
    The presence of any ``draft_worker_node:`` prefixed key is the
    xray success signal.
    """
    agent = _build_agent()
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("draft_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "draft_dispatch" in node_keys
    assert "draft_reduce_node" in node_keys
    assert "draft_node" not in node_keys
