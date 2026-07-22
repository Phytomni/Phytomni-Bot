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

import logging
from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneAgentState
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.brief_gene_states import (
    brief_gene_identity_fields,
    empty_brief_gene_annotation_fields,
)
from tests.support.subgraph_fakes import install_knowledge_app

from ._subgraph_branch_fakes import failing_async_object

pytestmark = pytest.mark.agent

_CORE_MODULE = "mcp_server_phytomni.agents.brief_gene.core"


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
            "retrieve_indexed_results": [],
        },
    )


def _gene_not_found_state() -> BriefGeneAgentState:
    """``gene_found=False`` state for the single-task fallback branch."""
    base = _gene_found_state()
    base["gene_found"] = False
    base["user_query"] = "Some free-text gene query"
    return base


# ---------------------------------------------------------------------------
# prep_tasks_node: per-gene fan-out task count.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# route_retrieve_tasks: Send dispatch list shape.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Worker factory: success + exception sentinel paths.
# ---------------------------------------------------------------------------


async def test_retrieve_worker_factory_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker on success writes ``(task_index, docs)`` tuple."""
    fake_app = _install_fake_knowledge_app(
        monkeypatch, docs_by_query={"q0": [{"title": "doc0"}]}
    )
    agent = _build_agent()
    worker = agent.make_retrieve_worker_node(fake_app)

    state = _gene_found_state()
    state["task_index"] = 0
    state["knowledge_input"] = {"user_query": "q0"}
    delta = await worker(state)

    assert delta["retrieve_indexed_results"] == [
        (0, [{"title": "doc0"}]),
    ]


async def test_retrieve_worker_factory_exception_writes_empty_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker on exception writes ``(task_index, [])`` AND logs loudly."""

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
    with caplog.at_level(logging.ERROR):
        delta = await worker(state)

    # Loud empty sentinel attributable to the specific failed task.
    assert delta["retrieve_indexed_results"] == [(7, [])]
    # The caught failure is logged loudly, not silently swallowed.
    assert "brief_gene retrieve worker failed: task_index=7" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    # The recovered fault also records a status-independent degraded entry
    # naming the failed leg's gene label, with a redacted message.
    assert delta["literature_degraded"] == [
        {"task_label": "OsTEST", "message": "boom"}
    ]


# ---------------------------------------------------------------------------
# reduce_node: sort + merge by score + top_n cap.
# ---------------------------------------------------------------------------


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
    state["retrieve_indexed_results"] = []
    delta = await agent.retrieve_reduce_node(state)

    assert delta["retrieved_docs"] == []


# ---------------------------------------------------------------------------
# Compile-time node sets: the Send-dispatch fan-out wire shape.
# ---------------------------------------------------------------------------


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
