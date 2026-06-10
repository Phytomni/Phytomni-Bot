# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the ``DeepResearchAgent`` review_results fan-out.

The review_results site routes through ``review_results_dispatch`` →
N × ``review_results_worker_node`` → ``review_results_reduce_node``
whose workers await ``CHAT_APP`` so xray expands the chat subgraph under
each worker. Also covers partial failure with the empty-JSON sentinel,
reduce ordering, and xray expansion.
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
    """Construct a ``DeepResearchAgent`` for the review_results fan-out."""
    config = ReviewConfig().model_copy(
        update={
            # Pin USE_KNOWLEDGE_SUBGRAPH False so the retrieve site keeps
            # its legacy ``retrieve_node`` and the test focuses on the
            # review_results fan-out wiring.
            "USE_KNOWLEDGE_SUBGRAPH": False,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _ok_chat_response(text: str) -> dict[str, Any]:
    """Build a minimal chat-completion response wrapping ``text``."""
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------------------
# Prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_review_results_prepare_tasks_node_returns_empty_delta() -> None:
    """``review_results_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["photosynthesis"],
            "draft_contents": ["draft-A"],
        },
    )
    result = await agent.review_results_prepare_tasks_node(state)
    assert result == {}


# ---------------------------------------------------------------------------
# Flag-on route_review_results_tasks returns N Send payloads.
# ---------------------------------------------------------------------------


def test_route_review_results_tasks_returns_n_sends() -> None:
    """``route_review_results_tasks`` returns one Send per draft entry."""
    agent = _build_agent()
    dimensions = ["photosynthesis", "chlorophyll", "stomatal"]
    drafts = ["draft-A", "draft-B", "draft-C"]
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": dimensions,
            "draft_contents": drafts,
        },
    )
    sends = agent.route_review_results_tasks(state)

    assert len(sends) == 3
    for i, (send, dim, draft) in enumerate(zip(sends, dimensions, drafts)):
        assert isinstance(send, Send)
        assert send.node == "review_results_worker_node"
        assert send.arg["task_index"] == i
        assert send.arg["subtopic"] == dim
        chat_payload = send.arg["chat_payload"]
        # The prompt-builder stitches current_subtopic / other_subtopics /
        # draft_text into the ``user/deep_research_review`` template; the
        # current dimension and its draft must appear in the user_query.
        assert dim in chat_payload["user_query"]
        assert draft in chat_payload["user_query"]
        assert "chat_kwargs" in chat_payload
        assert chat_payload["chat_kwargs"]["with_follow_up"] is False


# ---------------------------------------------------------------------------
# Flag-on worker success: writes (task_index, content) tuple.
# ---------------------------------------------------------------------------


async def test_review_results_worker_node_success_writes_indexed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``(task_index, content)`` on successful ainvoke."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(
            return_value={"response": _ok_chat_response("review-A")}
        )
    )
    monkeypatch.setattr(f"{_AGENT_MODULE}.CHAT_APP", fake_app)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 2,
            "subtopic": "auxin signalling",
            "chat_payload": {
                "user_query": "prompt",
                "chat_kwargs": {},
            },
        },
    )
    result = await agent.review_results_worker_node(state)

    assert result["review_indexed_results"] == [(2, "review-A")]
    assert "failures" not in result
    assert fake_app.ainvoke.await_count == 1


# ---------------------------------------------------------------------------
# Flag-on worker exception: writes "{}" sentinel AND FailureRecord.
# ---------------------------------------------------------------------------


async def test_review_results_worker_exception_writes_sentinel_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``"{}"`` sentinel AND FailureRecord dict on exception.

    The empty-JSON sentinel (not ``""``) preserves the legacy
    ``review_node`` substitution: downstream ``revise_node`` calls
    ``_extract_json_object`` on each critique and a non-JSON empty
    string would deviate from the legacy "no gaps" fallback shape.
    """
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
            "chat_payload": {
                "user_query": "prompt",
                "chat_kwargs": {},
            },
        },
    )
    result = await agent.review_results_worker_node(state)

    assert result["review_indexed_results"] == [(1, "{}")]
    failures = result.get("failures", [])
    assert len(failures) == 1
    rec = failures[0]
    # FailureRecord is a TypedDict — check structural keys, not isinstance.
    assert rec["kind"] == "execute"
    assert "chat timeout" in rec["message"]
    assert rec["task_label"] == "review_results:1"
    assert rec["traceback_digest"] is not None


# ---------------------------------------------------------------------------
# Flag-on reduce: sorts indexed_results by task_index before projecting.
# ---------------------------------------------------------------------------


async def test_review_results_reduce_node_sorts_by_task_index() -> None:
    """``review_results_reduce_node`` sorts results by task_index.

    Delivers the same ``review_contents`` ordering regardless of the
    order concurrent workers completed.
    """
    agent = _build_agent()
    # Supply results out-of-order (task 1 arrives before task 0).
    state = cast(
        DeepResearchState,
        {
            "review_indexed_results": [(2, "C"), (0, "A"), (1, "B")],
        },
    )
    result = await agent.review_results_reduce_node(state)

    assert result["review_contents"] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Flag-on partial failure (1 of N): reduce preserves the "{}" sentinel.
# ---------------------------------------------------------------------------


async def test_review_results_reduce_keeps_partial_failure_sentinel() -> None:
    """Reduce yields N entries with ``"{}"`` slot for the failed dimension.

    Downstream ``revise_node`` parses each critique via
    ``_extract_json_object`` and treats ``"{}"`` as "no gaps", so the
    sentinel slot must travel through reduce without being skipped or
    rewritten.
    """
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "review_indexed_results": [
                (0, "{}"),  # failed
                (1, '{"has_critical_gaps": false}'),
                (2, "{}"),  # failed
            ],
        },
    )
    result = await agent.review_results_reduce_node(state)

    assert result["review_contents"] == [
        "{}",
        '{"has_critical_gaps": false}',
        "{}",
    ]


# ---------------------------------------------------------------------------
# Structural: compiled graph has Send triad with xray-expanded worker key.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_has_review_results_send_triad() -> None:
    """Compiled graph has ``review_results_dispatch`` and reduce nodes.

    ``review_results_worker_node`` awaits the module-level ``CHAT_APP``
    (a ``CompiledStateGraph``), so LangGraph's xray render REPLACES the
    flat key with prefixed children (``review_results_worker_node:<child>``).
    A plain ``"review_results_worker_node"`` key would indicate xray did
    NOT discover the chat subgraph; the prefixed form is the success
    signal.
    """
    agent = _build_agent()
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "review_results_dispatch" in node_keys
    assert any(
        key.startswith("review_results_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "review_results_reduce_node" in node_keys
    assert "review_node" not in node_keys


# ---------------------------------------------------------------------------
# Xray: the chat subgraph expands UNDER ``review_results_worker_node``.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_chat_under_review_results_worker() -> (
    None
):
    """Flag-on graph exposes the chat subgraph to ``xray``.

    ``find_subgraph_pregel`` walks the worker's closure-free-variable
    path through ``__globals__`` to find the module-level ``CHAT_APP``
    and inlines it. LangGraph prefixes the inlined subgraph's child
    node keys with the PARENT node's ``add_node()`` name — i.e.
    ``review_results_worker_node:<child>``, NOT a literal ``chat:``
    namespace (the prefix is the parent node name, not the subgraph's
    own name).  The presence of any ``review_results_worker_node:``
    prefixed key is the xray success signal.
    """
    agent = _build_agent()
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("review_results_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "review_results_dispatch" in node_keys
    assert "review_results_reduce_node" in node_keys
    assert "review_node" not in node_keys
