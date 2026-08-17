# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for BriefGeneAgent's knowledge retrieval fan-out.

The retrieve site routes through a Send-dispatch triad
(``retrieve_prep_tasks_node`` → N × ``retrieve_worker_node`` →
``retrieve_reduce_node``) so each per-symbol task fans out to a
dedicated KnowledgeAgent subgraph invocation.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, TypedDict, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneAgentState
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RetrievalProtocolError,
)
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.brief_gene_states import (
    brief_gene_identity_fields,
    empty_brief_gene_annotation_fields,
)
from tests.support.logging_helpers import capture_non_propagating_logger
from tests.support.subgraph_fakes import install_knowledge_app

from ._subgraph_branch_fakes import failing_async_object

pytestmark = pytest.mark.agent

_CORE_MODULE = "mcp_server_phytomni.agents.brief_gene.core"
_KNOWLEDGE_SUBGRAPH_MODULE = (
    "mcp_server_phytomni.agents.brief_gene.graph_knowledge_subgraph"
)


class _TransientKnowledgeState(TypedDict, total=False):
    """Small real graph state for checkpoint lifecycle assertions."""

    user_query: str
    retrieved_docs: list[dict[str, Any]]
    retrieval_outcome: str
    final_response: dict[str, Any]


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
    docs_by_query: dict[str, list[dict[str, Any]]] | None = None,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` import on brief_gene.core."""
    return install_knowledge_app(
        monkeypatch,
        f"{_CORE_MODULE}.build_knowledge_app",
        docs_by_query=docs_by_query,
    ).compiled


def _build_agent(
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> BriefGeneAgent:
    """Construct a ``BriefGeneAgent`` with a faked knowledge app."""
    if monkeypatch is not None:
        _install_fake_knowledge_app(monkeypatch)
    config = BriefGeneConfig()
    return BriefGeneAgent(
        brief_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _gene_found_state() -> BriefGeneAgentState:
    """Minimal ``gene_found=True`` state with 2 deduplicated symbols."""
    return cast(
        BriefGeneAgentState,
        {
            "user_query": "AT1G01010",
            **brief_gene_identity_fields(),
            "species_all_name": "Arabidopsis thaliana",
            "gene_name_symbol_list": ["AT1G01010"],
            "gene_id_list": ["AT1G01010", "NAC001"],
            "gene_chr": "1",
            "gene_start": "3631",
            "gene_end": "5899",
            "gene_strand": "+",
            **empty_brief_gene_annotation_fields(),
            "retrieve_context": "",
            "follow_up_questions": [],
            "final_response": {},
            "retrieve_tasks": [
                {
                    "knowledge_input": {"user_query": f"q{i}"},
                    "task_label": f"task{i}",
                }
                for i in range(3)
            ],
            **{
                key: []
                for key in (
                    "retrieve_indexed_results",
                    "retrieve_failed_indices",
                    "retrieve_cancelled_indices",
                    "annotation_failed_indices",
                    "literature_degraded",
                )
            },
        },
    )


def _gene_not_found_state() -> BriefGeneAgentState:
    """``gene_found=False`` state for the single-task fallback branch."""
    base = _gene_found_state()
    base["gene_found"] = False
    base["user_query"] = "Some free-text gene query"
    return base


async def test_prep_tasks_node_gene_found_emits_n_plus_one_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_found=True with N=2 symbols emits 3 tasks (2 + combined)."""
    agent = _build_agent(monkeypatch=monkeypatch)
    delta = await agent.retrieve_prep_tasks_node(_gene_found_state())

    tasks = delta["retrieve_tasks"]
    # 2 deduplicated symbols + 1 combined-symbols query = 3 tasks.
    assert len(tasks) == 3
    user_queries = [t["knowledge_input"]["user_query"] for t in tasks]
    assert "Arabidopsis thaliana\nAT1G01010" in user_queries
    assert "Arabidopsis thaliana\nNAC001" in user_queries
    # Combined query joins the dedup'd symbols with newlines.
    combined_term = "AT1G01010\nNAC001"
    assert any(combined_term in q for q in user_queries)


async def test_prep_tasks_node_gene_not_found_emits_single_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_found=False emits a single task carrying the raw user_query."""
    agent = _build_agent(monkeypatch=monkeypatch)
    delta = await agent.retrieve_prep_tasks_node(_gene_not_found_state())

    tasks = delta["retrieve_tasks"]
    assert len(tasks) == 1
    ki = tasks[0]["knowledge_input"]
    assert ki["user_query"] == "Some free-text gene query"
    # Every task pins the retrieve-only path regardless of gene_found.
    assert ki["is_generate"] is False
    assert ki["is_follow_up"] is False


def test_route_retrieve_tasks_emits_one_send_per_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``route_retrieve_tasks`` returns one Send per staged task."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_tasks"] = [
        {"knowledge_input": {"user_query": f"q{i}"}, "task_label": f"gene{i}"}
        for i in range(3)
    ]
    sends = agent.route_retrieve_tasks(state)

    assert len(sends) == 3
    assert all(isinstance(s, Send) for s in sends)
    # Each Send routes to the worker and carries the per-task index.
    indices = sorted(s.arg["task_index"] for s in sends)
    assert indices == [0, 1, 2]


async def test_retrieve_worker_factory_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker on success writes ``(task_index, docs)`` tuple."""
    fake_app = _install_fake_knowledge_app(
        monkeypatch,
        docs_by_query={
            "q0": [{"chunk_id": "0", "title": "doc0", "content": "text"}]
        },
    )
    agent = _build_agent()
    worker = agent.make_retrieve_worker_node(fake_app)

    state = _gene_found_state()
    state["task_index"] = 0
    state["knowledge_input"] = {"user_query": "q0"}
    delta = await worker(state)

    assert delta["retrieve_indexed_results"] == [
        (0, [{"chunk_id": "0", "title": "doc0", "content": "text"}]),
    ]


async def test_retrieve_worker_reclaims_standard_graph_runner_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each mounted retrieval isolates and deletes its transient checkpoint."""
    failing_app = failing_async_object(
        "ainvoke",
        AssertionError("worker bypassed the standard graph runner"),
    )
    deleted_threads: list[str] = []

    async def delete_thread(thread_id: str) -> None:
        deleted_threads.append(thread_id)

    fake_app = SimpleNamespace(
        ainvoke=failing_app.ainvoke,
        checkpointer=SimpleNamespace(adelete_thread=delete_thread),
    )
    calls: list[tuple[object, object, str | None]] = []

    async def invoke(
        app: object,
        payload: object,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        calls.append((app, payload, thread_id))
        return {
            "retrieved_docs": [
                {"chunk_id": "3", "title": "isolated", "content": "text"}
            ],
            "retrieval_outcome": "complete",
            "final_response": {},
        }

    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ainvoke_graph",
        invoke,
    )
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ensure_thread_id",
        lambda: "brief-gene-worker-3",
        raising=False,
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, fake_app)
    )
    state = _gene_found_state()
    state["task_index"] = 3
    state["knowledge_input"] = {"user_query": "q3"}

    delta = await worker(state)

    assert calls == [(fake_app, {"user_query": "q3"}, "brief-gene-worker-3")]
    assert deleted_threads == ["brief-gene-worker-3"]
    assert delta["retrieve_indexed_results"] == [
        (
            3,
            [{"chunk_id": "3", "title": "isolated", "content": "text"}],
        ),
    ]


async def test_retrieve_worker_reclaims_checkpoint_after_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed transient retrieval still deletes its checkpoint thread."""
    deleted_threads: list[str] = []

    async def delete_thread(thread_id: str) -> None:
        deleted_threads.append(thread_id)

    fake_app = SimpleNamespace(
        checkpointer=SimpleNamespace(adelete_thread=delete_thread),
    )

    async def invoke(
        _app: object,
        _payload: object,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        assert thread_id == "brief-gene-worker-failed"
        raise RuntimeError("retrieval unavailable")

    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ainvoke_graph",
        invoke,
    )
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ensure_thread_id",
        lambda: "brief-gene-worker-failed",
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, fake_app)
    )
    state = _gene_found_state()
    state["task_index"] = 4
    state["task_label"] = "OsFAIL"
    state["knowledge_input"] = {"user_query": "q4"}

    delta = await worker(state)

    assert deleted_threads == ["brief-gene-worker-failed"]
    assert delta["retrieve_failed_indices"] == [4]
    assert "retrieve_indexed_results" not in delta
    assert delta["literature_degraded"] == [
        {"task_label": "OsFAIL", "message": "retrieval_unavailable"}
    ]


async def test_retrieve_worker_removes_real_memory_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real LangGraph saver retains no completed worker checkpoint."""

    async def retrieve(
        _state: _TransientKnowledgeState,
    ) -> dict[str, Any]:
        return {
            "retrieved_docs": [
                {
                    "chunk_id": "5",
                    "title": "persisted briefly",
                    "content": "text",
                }
            ],
            "retrieval_outcome": "complete",
            "final_response": {},
        }

    checkpointer = MemorySaver()
    workflow = StateGraph(_TransientKnowledgeState)
    workflow.add_node("retrieve", cast(Any, retrieve))
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", END)
    knowledge_app = workflow.compile(checkpointer=checkpointer)
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ensure_thread_id",
        lambda: "brief-gene-real-checkpoint",
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(knowledge_app)
    state = _gene_found_state()
    state["task_index"] = 5
    state["knowledge_input"] = {"user_query": "q5"}

    delta = await worker(state)

    assert delta["retrieve_indexed_results"] == [
        (
            5,
            [
                {
                    "chunk_id": "5",
                    "title": "persisted briefly",
                    "content": "text",
                }
            ],
        ),
    ]
    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "brief-gene-real-checkpoint"}},
    )
    assert await checkpointer.aget_tuple(config) is None


async def test_retrieve_worker_factory_exception_records_failed_index(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker records the failed index without manufacturing empty evidence."""

    broken_app = failing_async_object("ainvoke", RuntimeError("boom"))

    monkeypatch.setattr(
        f"{_CORE_MODULE}.build_knowledge_app",
        lambda **_kwargs: cast(CompiledStateGraph, broken_app),
    )
    agent = _build_agent()
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, broken_app)
    )

    state = _gene_found_state()
    state["task_index"] = 7
    state["task_label"] = "OsTEST"
    state["knowledge_input"] = {"user_query": "ignored"}
    with (
        capture_non_propagating_logger(
            _KNOWLEDGE_SUBGRAPH_MODULE, caplog.handler
        ),
        caplog.at_level(logging.WARNING, logger=_KNOWLEDGE_SUBGRAPH_MODULE),
    ):
        delta = await worker(state)

    assert delta["retrieve_failed_indices"] == [7]
    assert "retrieve_indexed_results" not in delta
    # The caught failure is logged without exposing exception text.
    assert "brief_gene retrieve worker failed: task_index=7" in caplog.text
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    assert "boom" not in caplog.text
    assert delta["literature_degraded"] == [
        {"task_label": "OsTEST", "message": "retrieval_unavailable"}
    ]


async def test_retrieve_worker_marks_partial_output_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial Knowledge result keeps docs and one safe internal marker."""
    partial_output: dict[str, object] = {
        "retrieved_docs": [
            {"chunk_id": "partial-1", "title": "partial", "content": "doc"}
        ],
        "retrieval_outcome": "partial",
        "final_response": {},
    }
    fake_app = SimpleNamespace()

    async def invoke(
        _app: object,
        _payload: object,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        assert thread_id
        return partial_output

    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ainvoke_graph",
        invoke,
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, fake_app)
    )
    state = _gene_found_state()
    state["task_index"] = 1
    state["task_label"] = "OsPARTIAL"

    delta = await worker(state)

    assert delta == {
        "retrieve_indexed_results": [(1, partial_output["retrieved_docs"])],
        "literature_degraded": [
            {"task_label": "OsPARTIAL", "message": "retrieval_unavailable"}
        ],
    }


async def test_retrieve_worker_factory_classifies_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled leg writes only its private cancellation classification."""

    async def cancel(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    cancelled_app = SimpleNamespace(ainvoke=cancel)
    monkeypatch.setattr(
        f"{_CORE_MODULE}.build_knowledge_app",
        lambda **_kwargs: cast(CompiledStateGraph, cancelled_app),
    )
    agent = _build_agent()
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, cancelled_app)
    )
    state = _gene_found_state()
    state["task_index"] = 1
    state["task_label"] = "OsCANCELLED"
    state["knowledge_input"] = {"user_query": "ignored"}

    delta = await worker(state)

    assert delta == {"retrieve_cancelled_indices": [1]}


async def test_retrieve_worker_cleanup_failure_cannot_mask_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint cleanup failure cannot downgrade a cancelled retrieve leg."""

    async def invoke(
        _app: object,
        _payload: object,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        assert thread_id == "brief-gene-worker-cancelled"
        raise asyncio.CancelledError

    async def delete_thread(_thread_id: str) -> None:
        raise RuntimeError("private cleanup failure")

    fake_app = SimpleNamespace(
        checkpointer=SimpleNamespace(adelete_thread=delete_thread),
    )
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ainvoke_graph",
        invoke,
    )
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.ensure_thread_id",
        lambda: "brief-gene-worker-cancelled",
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, fake_app)
    )
    state = _gene_found_state()
    state["task_index"] = 1
    state["task_label"] = "OsCANCELLED"

    delta = await worker(state)

    assert delta == {"retrieve_cancelled_indices": [1]}


async def test_retrieve_reduce_node_sorts_by_task_index_and_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce sorts tuples by task_index, merges, sorts docs by score."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    # Workers complete in non-monotonic order; reducer must restore
    # task_index ordering before merging.
    state["retrieve_indexed_results"] = [
        (2, [{"title": "doc2-low", "score": 0.2}]),
        (0, [{"title": "doc0-high", "score": 0.9}]),
        (1, [{"title": "doc1-mid", "score": 0.5}]),
    ]
    delta = await agent.retrieve_reduce_node(state)

    docs = delta["retrieved_docs"]
    # Sorted by score descending after the per-index merge.
    assert [d["title"] for d in docs] == [
        "doc0-high",
        "doc1-mid",
        "doc2-low",
    ]
    assert "retrieve_context" in delta


async def test_retrieve_reduce_node_handles_empty_indexed_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce on an empty reducer channel returns empty docs."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [
        (0, []),
        (1, []),
        (2, []),
    ]
    delta = await agent.retrieve_reduce_node(state)

    assert delta["retrieved_docs"] == []


async def test_retrieve_reduce_rejects_missing_result_or_failure_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every planned retrieve leg must be classified exactly once."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [(0, [])]

    with pytest.raises(RetrievalProtocolError):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_fails_when_all_independent_evidence_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All literature and annotation failures produce one safe error."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [(0, []), (1, []), (2, [])]
    state["annotation_failed_indices"] = list(range(6))

    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_propagates_any_classified_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cancelled leg stops reduction even when sibling docs succeeded."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [
        (0, [{"title": "reliable sibling", "score": 0.9}]),
        (1, []),
    ]
    state["retrieve_cancelled_indices"] = [2]

    with pytest.raises(asyncio.CancelledError):
        await agent.retrieve_reduce_node(state)


async def test_retrieve_reduce_progress_counts_success_and_failure_legs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial retrieval tick counts every completed non-cancelled leg."""
    events: list[tuple[str, int, int | None]] = []
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.emit_progress",
        lambda phase, current, total=None, **_kwargs: events.append(
            (phase, current, total)
        ),
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [(0, []), (1, [])]
    state["retrieve_failed_indices"] = [2]

    await agent.retrieve_reduce_node(state)

    assert events == [("retrieving", 3, 3)]


async def test_retrieve_reduce_cancellation_emits_no_completion_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled retrieval never advertises a normally completed stage."""
    events: list[tuple[str, int, int | None]] = []
    monkeypatch.setattr(
        f"{_KNOWLEDGE_SUBGRAPH_MODULE}.emit_progress",
        lambda phase, current, total=None, **_kwargs: events.append(
            (phase, current, total)
        ),
    )
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [(0, []), (1, [])]
    state["retrieve_cancelled_indices"] = [2]

    with pytest.raises(asyncio.CancelledError):
        await agent.retrieve_reduce_node(state)

    assert not events


@pytest.mark.parametrize(
    ("indexed", "failed", "annotation_failed", "expected_titles"),
    [
        (
            [(0, [{"title": "partial", "score": 0.8}]), (1, [])],
            [2],
            [],
            ["partial"],
        ),
        ([], [0, 1, 2], [], []),
        (
            [
                (0, [{"title": "annotation-backed", "score": 0.9}]),
                (1, []),
                (2, []),
            ],
            [],
            [1, 4],
            ["annotation-backed"],
        ),
        (
            [
                (0, [{"title": "literature-backed", "score": 0.7}]),
                (1, []),
                (2, []),
            ],
            [],
            list(range(6)),
            ["literature-backed"],
        ),
    ],
)
async def test_retrieve_reduce_preserves_remaining_independent_evidence(
    monkeypatch: pytest.MonkeyPatch,
    indexed: list[tuple[int, list[dict[str, Any]]]],
    failed: list[int],
    annotation_failed: list[int],
    expected_titles: list[str],
) -> None:
    """Partial, sparse, and no-match evidence remain distinct from failure."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = indexed
    state["retrieve_failed_indices"] = failed
    state["annotation_failed_indices"] = annotation_failed

    delta = await agent.retrieve_reduce_node(state)

    assert [doc["title"] for doc in delta["retrieved_docs"]] == expected_titles


@pytest.mark.parametrize(
    ("indexed", "failed", "cancelled"),
    [
        ([(True, []), (1, []), (2, [])], [], []),
        ([(0, []), (0, []), (1, []), (2, [])], [], []),
        ([(0, []), (1, []), (3, [])], [], []),
        ([(0, []), (1, [])], [True], []),
        ([(0, []), (1, [])], [2, 2], []),
        ([(0, []), (1, [])], [3], []),
        ([(0, []), (1, []), (2, [])], [2], []),
        ([(0, []), (1, [])], [], [True]),
        ([(0, []), (1, [])], [], [2, 2]),
        ([(0, []), (1, [])], [], [3]),
        ([(0, []), (1, []), (2, [])], [], [2]),
        ([(0, [])], [1], [1, 2]),
    ],
)
async def test_retrieve_reduce_rejects_ambiguous_or_out_of_range_indices(
    monkeypatch: pytest.MonkeyPatch,
    indexed: list[tuple[int, list[dict[str, Any]]]],
    failed: list[int],
    cancelled: list[int],
) -> None:
    """Duplicate, overlapping, and out-of-range classifications are invalid."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = indexed
    state["retrieve_failed_indices"] = failed
    state["retrieve_cancelled_indices"] = cancelled

    with pytest.raises(RetrievalProtocolError):
        await agent.retrieve_reduce_node(state)


@pytest.mark.parametrize("annotation_failed", [[0, 0], [-1], [6], [True]])
async def test_retrieve_reduce_rejects_invalid_annotation_failure_ordinals(
    monkeypatch: pytest.MonkeyPatch,
    annotation_failed: list[int],
) -> None:
    """Annotation failures expose only unique bounded table ordinals."""
    agent = _build_agent(monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = [(0, []), (1, []), (2, [])]
    state["annotation_failed_indices"] = annotation_failed

    with pytest.raises(RetrievalProtocolError):
        await agent.retrieve_reduce_node(state)


def test_compiled_graph_registers_fan_out_triad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph registers prep_tasks + worker + reduce (no legacy node)."""
    agent = _build_agent(monkeypatch=monkeypatch)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "retrieve_prep_tasks_node" in nodes
    assert "retrieve_worker_node" in nodes
    assert "retrieve_reduce_node" in nodes
    assert "retrieve_node" not in nodes


def test_compiled_graph_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xray=1 surfaces ``retrieve_worker_node:`` prefixed child keys."""
    agent = _build_agent(monkeypatch=monkeypatch)
    nodes = list(agent.app.get_graph(xray=1).nodes.keys())

    worker_children = [
        n for n in nodes if n.startswith("retrieve_worker_node:")
    ]
    assert worker_children, (
        "Expected the knowledge subgraph to expand under "
        "retrieve_worker_node at xray=1 "
        f"(saw nodes: {sorted(nodes)})"
    )


def test_compiled_graph_chat_and_knowledge_subgraphs_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat + knowledge subgraphs coexist under xray."""
    agent = _build_agent(monkeypatch=monkeypatch)
    nodes = list(agent.app.get_graph(xray=1).nodes.keys())

    # Chat subgraph block under the shared chat mount.
    assert any(
        n.startswith("chat:") for n in nodes
    ), f"Missing chat: prefix at xray=1 (saw nodes: {sorted(nodes)})"
    # Knowledge subgraph block under each Send-dispatched worker.
    assert any(n.startswith("retrieve_worker_node:") for n in nodes), (
        "Missing retrieve_worker_node: prefix at xray=1 "
        f"(saw nodes: {sorted(nodes)})"
    )
