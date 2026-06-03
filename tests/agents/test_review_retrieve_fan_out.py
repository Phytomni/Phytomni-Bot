# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for ``DeepResearchAgent`` knowledge retrieval fan-out.

Pins ``USE_KNOWLEDGE_SUBGRAPH``: flag-off keeps the legacy
``retrieve_node`` which calls ``ka.arun`` via ``asyncio.gather``;
flag-on (behind ``USE_CHAT_SUBGRAPH=True``) routes through a
Send-dispatch triad (``retrieve_dispatch`` → N ×
``retrieve_worker_node`` → ``retrieve_reduce_node``). Also covers
partial failure, reduce ordering, and xray subgraph expansion.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_AGENT_MODULE = "mcp_server_phytomni.agents.review.agent"


class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    retrieved_docs: list[dict[str, Any]]


def _build_fake_knowledge_app() -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for the KA subgraph.

    ``find_subgraph_pregel`` recognises ``CompiledStateGraph`` instances
    by isinstance, not by duck typing, so a ``SimpleNamespace`` cannot
    satisfy the xray expansion. Compiling a trivial ``StateGraph`` that
    returns an empty ``retrieved_docs`` list keeps the test fully offline
    while still presenting a real compiled subgraph for the worker's
    ``self._knowledge_app`` attribute to hold.
    """

    async def _noop(state: _FakeKnowledgeState) -> dict[str, Any]:
        del state
        return {"retrieved_docs": []}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("noop", _noop)
    workflow.add_edge(START, "noop")
    workflow.add_edge("noop", END)
    return workflow.compile()


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` to return a deterministic stub."""
    fake_app = _build_fake_knowledge_app()
    monkeypatch.setattr(
        f"{_AGENT_MODULE}.build_knowledge_app", lambda **_kwargs: fake_app
    )
    return fake_app


def _build_agent(
    use_knowledge_subgraph: bool,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    use_chat_subgraph: bool = True,
) -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` with the specified flag combination.

    ``USE_CHAT_SUBGRAPH=True`` is the prerequisite for
    ``USE_KNOWLEDGE_SUBGRAPH`` to have any effect (the Send-based fan-out
    only wires into ``_wire_chat_subgraph``). The default here mirrors
    the intended production pairing.
    """
    if use_knowledge_subgraph and monkeypatch is not None:
        _install_fake_knowledge_app(monkeypatch)
    config = ReviewConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": use_knowledge_subgraph,
            "USE_CHAT_SUBGRAPH": use_chat_subgraph,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Flag-off legacy: ``retrieve_node`` still gathers via ``ka.arun``.
# ---------------------------------------------------------------------------


def test_retrieve_node_flag_off_graph_keeps_legacy_node() -> None:
    """Flag-off compiled graph has ``retrieve_node`` and no Send triad."""
    agent = _build_agent(use_knowledge_subgraph=False)
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "retrieve_node" in node_keys
    assert "retrieve_dispatch" not in node_keys
    assert "retrieve_worker_node" not in node_keys
    assert "retrieve_reduce_node" not in node_keys


# ---------------------------------------------------------------------------
# Flag-on prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_retrieve_prepare_tasks_node_returns_empty_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``retrieve_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    state = cast(
        DeepResearchState,
        {"research_dimensions": ["photosynthesis", "chlorophyll"]},
    )
    result = await agent.retrieve_prepare_tasks_node(state)
    assert result == {}


# ---------------------------------------------------------------------------
# Flag-on route_retrieve_tasks returns N Send payloads.
# ---------------------------------------------------------------------------


def test_route_retrieve_tasks_returns_n_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``route_retrieve_tasks`` returns one Send per research dimension."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    dimensions = ["photosynthesis", "chlorophyll", "stomatal conductance"]
    state = cast(DeepResearchState, {"research_dimensions": dimensions})
    sends = agent.route_retrieve_tasks(state)

    assert len(sends) == 3
    for i, (send, dim) in enumerate(zip(sends, dimensions)):
        assert isinstance(send, Send)
        assert send.node == "retrieve_worker_node"
        assert send.arg["task_index"] == i
        assert send.arg["dimension"] == dim
        payload = send.arg["knowledge_payload"]
        assert payload["user_query"] == dim
        assert payload["is_generate"] is False
        assert payload["is_follow_up"] is False


# ---------------------------------------------------------------------------
# Flag-on worker success: writes (task_index, docs) tuple.
# ---------------------------------------------------------------------------


async def test_retrieve_worker_node_success_writes_indexed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``(task_index, docs)`` on successful ainvoke."""
    docs = [{"title": "Doc A", "content": "content-A"}]
    fake_app = AsyncMock(
        ainvoke=AsyncMock(return_value={"retrieved_docs": docs})
    )
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(fake_app)
    state = cast(
        DeepResearchState,
        {
            "task_index": 2,
            "dimension": "auxin signalling",
            "knowledge_payload": {
                "user_query": "auxin signalling",
                "is_generate": False,
                "is_follow_up": False,
                "repo_id_dict": {},
            },
        },
    )
    result = await worker(state)

    assert result["retrieve_indexed_results"] == [(2, docs)]
    assert "failures" not in result


# ---------------------------------------------------------------------------
# Flag-on worker exception: writes empty sentinel AND FailureRecord.
# ---------------------------------------------------------------------------


async def test_retrieve_worker_node_exception_writes_sentinel_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``[]`` sentinel AND FailureRecord dict on exception."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(side_effect=RuntimeError("backend timeout"))
    )
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(fake_app)
    state = cast(
        DeepResearchState,
        {
            "task_index": 1,
            "dimension": "drought tolerance",
            "knowledge_payload": {"user_query": "drought tolerance"},
        },
    )
    result = await worker(state)

    assert result["retrieve_indexed_results"] == [(1, [])]
    failures = result.get("failures", [])
    assert len(failures) == 1
    rec = failures[0]
    # FailureRecord is a TypedDict — check structural keys, not isinstance.
    assert rec["kind"] == "execute"
    assert "backend timeout" in rec["message"]
    assert rec["task_label"] == "retrieve:1"


# ---------------------------------------------------------------------------
# Flag-on reduce: sorts indexed_results by task_index before iterating.
# ---------------------------------------------------------------------------


async def test_retrieve_reduce_node_sorts_by_task_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``retrieve_reduce_node`` sorts results by task_index before reducing.

    Delivers the same ``dimension_params`` ordering regardless of the
    order concurrent workers completed.
    """
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    # Supply results out-of-order (task 1 arrives before task 0).
    doc_0 = {"title": "D0", "content": "c0"}
    doc_1 = {"title": "D1", "content": "c1"}
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["dim0", "dim1"],
            "total_length": 0,
            "retrieve_indexed_results": [(1, [doc_1]), (0, [doc_0])],
        },
    )
    result = await agent.retrieve_reduce_node(state)

    params = result["dimension_params"]
    assert len(params) == 2
    # After sort, index-0 dimension appears first.
    assert params[0]["subtopic"] == "dim0"
    assert params[1]["subtopic"] == "dim1"


# ---------------------------------------------------------------------------
# Flag-on partial failure (1 of N): reduce produces N dimension_params.
# ---------------------------------------------------------------------------


async def test_retrieve_reduce_node_partial_failure_still_produces_n_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce yields N dimension_params even when one worker returned ``[]``.

    The failed dimension gets empty fragments; the reduce does not skip
    it, so ``draft_node`` still receives a slot for every dimension.
    """
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    doc = {"title": "D2", "content": "c2"}
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1", "d2"],
            "total_length": 0,
            "retrieve_indexed_results": [
                (0, []),  # failed
                (1, [doc]),
                (2, []),  # failed
            ],
        },
    )
    result = await agent.retrieve_reduce_node(state)

    params = result["dimension_params"]
    assert len(params) == 3
    assert params[0]["subtopic"] == "d0"
    assert params[1]["subtopic"] == "d1"
    assert params[2]["subtopic"] == "d2"


# ---------------------------------------------------------------------------
# Constructor: ``_knowledge_app`` is only built when the flag is on.
# ---------------------------------------------------------------------------


def test_knowledge_app_built_only_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_knowledge_app`` is ``None`` flag-off, populated flag-on."""
    agent_off = _build_agent(
        use_knowledge_subgraph=False, monkeypatch=monkeypatch
    )
    assert agent_off._knowledge_app is None

    fake_app = _install_fake_knowledge_app(monkeypatch)
    agent_on = DeepResearchAgent(
        review_config=ReviewConfig().model_copy(
            update={
                "USE_KNOWLEDGE_SUBGRAPH": True,
                "USE_CHAT_SUBGRAPH": True,
            }
        ),
        sensitive_config=SensitiveConfig.load(),
    )
    assert agent_on._knowledge_app is fake_app


# ---------------------------------------------------------------------------
# Structural: flag-on graph has Send triad; flag-off keeps legacy.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_has_send_triad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on graph has ``retrieve_dispatch``, worker, and reduce nodes.

    ``retrieve_worker_node`` registers a ``CompiledStateGraph`` via the
    factory closure, so LangGraph's xray render REPLACES the flat key
    with prefixed children (``retrieve_worker_node:<child>``). A plain
    ``"retrieve_worker_node"`` key would indicate xray did NOT discover
    the subgraph; the prefixed form is the success signal.
    """
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "retrieve_dispatch" in node_keys
    assert any(
        key.startswith("retrieve_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "retrieve_reduce_node" in node_keys
    assert "retrieve_node" not in node_keys


# ---------------------------------------------------------------------------
# Xray: the KnowledgeAgent subgraph expands UNDER ``retrieve_worker_node``.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on graph exposes the KA subgraph to ``xray``.

    ``find_subgraph_pregel`` walks the worker closure's free variable to
    find the compiled ``_knowledge_app`` and inlines it. LangGraph
    prefixes the inlined subgraph's child node keys with the PARENT
    node's ``add_node()`` name — i.e. ``retrieve_worker_node:<child>``,
    not a literal ``knowledge:`` namespace. The presence of any
    ``retrieve_worker_node:`` prefixed key is the xray success signal;
    a plain flat ``retrieve_worker_node`` key would mean the walker
    failed to find the subgraph.
    """
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("retrieve_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "retrieve_dispatch" in node_keys
    assert "retrieve_reduce_node" in node_keys
    assert "retrieve_node" not in node_keys
