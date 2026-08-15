# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``DeepResearchAgent`` knowledge retrieval fan-out.

The retrieve site routes through a Send-dispatch triad
(``retrieve_dispatch`` → N × ``retrieve_worker_node`` →
``retrieve_reduce_node``). Also covers partial failure, reduce
ordering, and xray subgraph expansion.
"""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Send
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RetrievalProtocolError,
)
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.subgraph_fakes import install_knowledge_app

pytestmark = pytest.mark.agent

_AGENT_MODULE = "mcp_server_phytomni.agents.review.agent"


def _install_fake_knowledge_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``build_knowledge_app`` to return a deterministic stub."""
    install_knowledge_app(
        monkeypatch,
        f"{_AGENT_MODULE}.build_knowledge_app",
    )


def _build_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` with the Send-based fan-out.

    The knowledge subgraph is always mounted at the retrieve site via
    the Send-dispatch triad; ``_install_fake_knowledge_app`` keeps the
    per-instance KA compile deterministic and offline.
    """
    _install_fake_knowledge_app(monkeypatch)
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_retrieve_prepare_tasks_node_returns_empty_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``retrieve_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {"research_dimensions": ["photosynthesis", "chlorophyll"]},
    )
    result = await agent.retrieve_prepare_tasks_node(state)
    assert result == {}


# ---------------------------------------------------------------------------
# route_retrieve_tasks returns N Send payloads.
# ---------------------------------------------------------------------------


def test_route_retrieve_tasks_returns_n_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``route_retrieve_tasks`` returns one Send per research dimension."""
    agent = _build_agent(monkeypatch)
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
# Worker success: writes (task_index, docs) tuple.
# ---------------------------------------------------------------------------


async def test_retrieve_worker_node_success_writes_indexed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``(task_index, docs)`` on successful ainvoke."""
    docs = [
        {
            "chunk_id": "doc-a-1",
            "title": "Doc A",
            "content": "content-A",
        }
    ]
    fake_app = AsyncMock(
        ainvoke=AsyncMock(
            return_value={
                "retrieved_docs": docs,
                "retrieval_outcome": "complete",
                "final_response": {},
            }
        )
    )
    agent = _build_agent(monkeypatch)
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
# Worker exception: writes only the internal failed-index accumulator.
# ---------------------------------------------------------------------------


async def test_retrieve_worker_node_exception_writes_failed_index(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker does not turn a retrieval failure into valid empty evidence."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(side_effect=RuntimeError("backend timeout"))
    )
    agent = _build_agent(monkeypatch)
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

    assert result == {"retrieve_failed_indices": [1]}
    assert "backend timeout" not in caplog.text


async def test_retrieve_worker_reraises_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is never recorded as a failed retrieval."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(side_effect=asyncio.CancelledError())
    )
    agent = _build_agent(monkeypatch)
    worker = agent.make_retrieve_worker_node(fake_app)

    with pytest.raises(asyncio.CancelledError):
        await worker(
            cast(
                DeepResearchState,
                {
                    "task_index": 1,
                    "knowledge_payload": {"user_query": "drought"},
                },
            )
        )


# ---------------------------------------------------------------------------
# Reduce: sorts indexed_results by task_index before iterating.
# ---------------------------------------------------------------------------


async def test_retrieve_reduce_node_sorts_by_task_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``retrieve_reduce_node`` sorts results by task_index before reducing.

    Delivers the same ``dimension_params`` ordering regardless of the
    order concurrent workers completed.
    """
    agent = _build_agent(monkeypatch)
    # Supply results out-of-order (task 1 arrives before task 0).
    doc_0 = {"title": "D0", "content": "c0"}
    doc_1 = {"title": "D1", "content": "c1"}
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["dim0", "dim1"],
            "total_length": 0,
            "retrieve_indexed_results": [(1, [doc_1]), (0, [doc_0])],
            "retrieve_failed_indices": [],
        },
    )
    result = await agent.retrieve_reduce_node(state)

    params = result["dimension_params"]
    assert len(params) == 2
    # After sort, index-0 dimension appears first.
    assert params[0]["subtopic"] == "dim0"
    assert "c0" in params[0]["knowledge"]
    assert params[1]["subtopic"] == "dim1"
    assert "c1" in params[1]["knowledge"]


# ---------------------------------------------------------------------------
# Partial failure (1 of N): reduce produces N dimension_params.
# ---------------------------------------------------------------------------


async def test_retrieve_reduce_node_partial_failure_still_produces_n_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce yields N dimension_params when one worker failed.

    The failed dimensions get empty fragments; the reduce does not skip
    them while reliable evidence from the remaining dimension is retained.
    """
    agent = _build_agent(monkeypatch)
    doc = {"title": "D2", "content": "c2"}
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1", "d2"],
            "total_length": 0,
            "retrieve_indexed_results": [
                (1, [doc]),
            ],
            "retrieve_failed_indices": [0, 2],
        },
    )
    result = await agent.retrieve_reduce_node(state)

    params = result["dimension_params"]
    assert len(params) == 3
    assert params[0]["subtopic"] == "d0"
    assert params[1]["subtopic"] == "d1"
    assert params[2]["subtopic"] == "d2"


async def test_retrieve_reduce_rejects_missing_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent success/failure index is a retrieval protocol error."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1"],
            "total_length": 0,
            "retrieve_indexed_results": [(0, [])],
            "retrieve_failed_indices": [],
        },
    )

    with pytest.raises(RetrievalProtocolError):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_accepts_valid_empty_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete empty successes remain genuine no-match classifications."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1"],
            "total_length": 0,
            "retrieve_indexed_results": [(1, []), (0, [])],
            "retrieve_failed_indices": [],
        },
    )

    result = await agent.retrieve_reduce_node(state)

    assert result["all_raw_doc_list"] == []
    assert result["dimension_params"] == [
        {"subtopic": "d0", "knowledge": ""},
        {"subtopic": "d1", "knowledge": ""},
    ]


@pytest.mark.parametrize(
    ("indexed", "failed"),
    [
        ([(0, []), (0, [])], [1]),
        ([(0, [])], [1, 1]),
        ([(0, []), (1, [])], [1]),
        ([(0, []), (2, [])], []),
        ([(0, [])], [2]),
        ([(False, []), (1, [])], []),
        ([(0, [])], [True]),
    ],
    ids=[
        "duplicate-success",
        "duplicate-failure",
        "success-failure-overlap",
        "success-out-of-range",
        "failure-out-of-range",
        "boolean-success-index",
        "boolean-failure-index",
    ],
)
async def test_retrieve_reduce_rejects_invalid_index_classifications(
    monkeypatch: pytest.MonkeyPatch,
    indexed: list[tuple[int, list[dict[str, object]]]],
    failed: list[int],
) -> None:
    """Every planned dimension must have one bounded integer classification."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1"],
            "total_length": 0,
            "retrieve_indexed_results": indexed,
            "retrieve_failed_indices": failed,
        },
    )

    with pytest.raises(RetrievalProtocolError):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_fails_when_no_reliable_evidence_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed dimension plus only no-match results cannot draft a report."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1"],
            "total_length": 0,
            "retrieve_indexed_results": [(1, [])],
            "retrieve_failed_indices": [0],
        },
    )

    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_fails_when_every_dimension_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An all-failed classification stops before Review drafting."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["d0", "d1"],
            "total_length": 0,
            "retrieve_indexed_results": [],
            "retrieve_failed_indices": [1, 0],
        },
    )

    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await agent.retrieve_reduce_node(state)


# ---------------------------------------------------------------------------
# Structural: compiled graph has the retrieve Send triad.
# ---------------------------------------------------------------------------


def test_compiled_graph_has_send_triad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph has ``retrieve_dispatch``, worker, and reduce nodes.

    ``retrieve_worker_node`` registers a ``CompiledStateGraph`` via the
    factory closure, so LangGraph's xray render REPLACES the flat key
    with prefixed children (``retrieve_worker_node:<child>``). A plain
    ``"retrieve_worker_node"`` key would indicate xray did NOT discover
    the subgraph; the prefixed form is the success signal.
    """
    agent = _build_agent(monkeypatch)
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


def test_compiled_graph_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph exposes the KA subgraph to ``xray``.

    ``find_subgraph_pregel`` walks the worker closure's free variable to
    find the compiled ``_knowledge_app`` and inlines it. LangGraph
    prefixes the inlined subgraph's child node keys with the PARENT
    node's ``add_node()`` name — i.e. ``retrieve_worker_node:<child>``,
    not a literal ``knowledge:`` namespace. The presence of any
    ``retrieve_worker_node:`` prefixed key is the xray success signal;
    a plain flat ``retrieve_worker_node`` key would mean the walker
    failed to find the subgraph.
    """
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("retrieve_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "retrieve_dispatch" in node_keys
    assert "retrieve_reduce_node" in node_keys
    assert "retrieve_node" not in node_keys
