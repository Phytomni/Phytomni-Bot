# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for BriefGeneAgent's knowledge retrieval fan-out.

Pins ``USE_KNOWLEDGE_SUBGRAPH``: flag-off keeps the legacy
``retrieve_node``; flag-on routes through a Send-dispatch triad
(``retrieve_prep_tasks_node`` → N × ``retrieve_worker_node`` →
``retrieve_reduce_node``) so each per-symbol task fans out to a
dedicated KnowledgeAgent subgraph invocation.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneAgentState
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_CORE_MODULE = "mcp_server_phytomni.agents.brief_gene.core"


class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    user_query: str
    retrieved_docs: list[dict[str, Any]]


def _build_fake_knowledge_app(
    docs_by_query: dict[str, list[dict[str, Any]]] | None = None,
) -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for the KA subgraph.

    ``find_subgraph_pregel`` recognises ``CompiledStateGraph`` instances
    by isinstance, so a SimpleNamespace cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` keeps the test fully
    offline while still presenting a real compiled subgraph for the
    worker factory closure to hold. The optional ``docs_by_query`` map
    lets a test stage per-query doc lists keyed on the
    ``user_query`` substring; absent keys default to ``[]``.
    """

    async def _stub(state: _FakeKnowledgeState) -> dict[str, Any]:
        if docs_by_query is None:
            return {"retrieved_docs": []}
        query = cast(Any, state).get("user_query", "")
        return {"retrieved_docs": docs_by_query.get(query, [])}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("stub", _stub)
    workflow.add_edge(START, "stub")
    workflow.add_edge("stub", END)
    return workflow.compile()


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
    docs_by_query: dict[str, list[dict[str, Any]]] | None = None,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` import on brief_gene.core."""
    fake_app = _build_fake_knowledge_app(docs_by_query)
    monkeypatch.setattr(
        f"{_CORE_MODULE}.build_knowledge_app", lambda **_kwargs: fake_app
    )
    return fake_app


def _build_agent(
    use_knowledge_subgraph: bool,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    use_chat_subgraph: bool = False,
) -> BriefGeneAgent:
    """Construct a ``BriefGeneAgent`` with the specified flag combination."""
    if use_knowledge_subgraph and monkeypatch is not None:
        _install_fake_knowledge_app(monkeypatch)
    config = BriefGeneConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": use_knowledge_subgraph,
            "USE_CHAT_SUBGRAPH": use_chat_subgraph,
        }
    )
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
            "is_follow_up": False,
            "gene_found": True,
            "gene_id": "AT1G01010",
            "query_id_version": "tair10",
            "gene_id_version": "tair10",
            "species_code": "ath",
            "species_latin_name": "Arabidopsis thaliana",
            "species_english_name": "thale cress",
            "species_all_name": "Arabidopsis thaliana",
            "gene_name_symbol_list": ["AT1G01010"],
            "gene_id_list": ["AT1G01010", "NAC001"],
            "gene_chr": "1",
            "gene_start": "3631",
            "gene_end": "5899",
            "gene_strand": "+",
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "retrieved_docs": [],
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
# Constructor: _knowledge_app built only on flag-on.
# ---------------------------------------------------------------------------


def test_knowledge_app_built_only_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_knowledge_app`` is ``None`` flag-off, populated flag-on."""
    agent_off = _build_agent(use_knowledge_subgraph=False)
    assert agent_off._knowledge_app is None

    fake_app = _install_fake_knowledge_app(monkeypatch)
    agent_on = _build_agent(use_knowledge_subgraph=True)
    assert agent_on._knowledge_app is fake_app


# ---------------------------------------------------------------------------
# prep_tasks_node: per-gene fan-out task count.
# ---------------------------------------------------------------------------


async def test_prep_tasks_node_gene_found_emits_n_plus_one_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_found=True with N=2 symbols emits 3 tasks (2 + combined)."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
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
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
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
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_tasks"] = [
        {"knowledge_input": {"user_query": f"q{i}"}} for i in range(3)
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
    agent = _build_agent(use_knowledge_subgraph=True)
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
) -> None:
    """Worker on knowledge-app exception writes ``(task_index, [])``."""

    async def _raising_ainvoke(_input: Any) -> Any:
        raise RuntimeError("boom")

    class _BrokenApp:
        ainvoke = staticmethod(_raising_ainvoke)

    monkeypatch.setattr(
        f"{_CORE_MODULE}.build_knowledge_app",
        lambda **_kwargs: cast(CompiledStateGraph, _BrokenApp()),
    )
    agent = _build_agent(use_knowledge_subgraph=True)
    worker = agent.make_retrieve_worker_node(
        cast(CompiledStateGraph, _BrokenApp())
    )

    state = _gene_found_state()
    state["task_index"] = 7
    state["knowledge_input"] = {"user_query": "ignored"}
    delta = await worker(state)

    # Loud empty sentinel attributable to the specific failed task.
    assert delta["retrieve_indexed_results"] == [(7, [])]


# ---------------------------------------------------------------------------
# reduce_node: sort + merge by score + top_n cap.
# ---------------------------------------------------------------------------


async def test_retrieve_reduce_node_sorts_by_task_index_and_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce sorts tuples by task_index, merges, sorts docs by score."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
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
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    state = _gene_found_state()
    state["retrieve_indexed_results"] = []
    delta = await agent.retrieve_reduce_node(state)

    assert delta["retrieved_docs"] == []


# ---------------------------------------------------------------------------
# Compile-time node sets: flag-off vs flag-on wire shapes.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_off_keeps_legacy_retrieve_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off graph registers ``retrieve_node`` (no fan-out triad)."""
    del monkeypatch
    agent = _build_agent(use_knowledge_subgraph=False)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "retrieve_node" in nodes
    assert "retrieve_prep_tasks_node" not in nodes
    assert "retrieve_worker_node" not in nodes
    assert "retrieve_reduce_node" not in nodes


def test_compiled_graph_flag_on_registers_fan_out_triad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on graph registers prep_tasks + worker + reduce."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "retrieve_prep_tasks_node" in nodes
    assert "retrieve_worker_node" in nodes
    assert "retrieve_reduce_node" in nodes
    assert "retrieve_node" not in nodes


def test_compiled_graph_flag_on_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xray=1 surfaces ``retrieve_worker_node:`` prefixed child keys."""
    agent = _build_agent(use_knowledge_subgraph=True, monkeypatch=monkeypatch)
    nodes = list(agent.app.get_graph(xray=1).nodes.keys())

    worker_children = [
        n for n in nodes if n.startswith("retrieve_worker_node:")
    ]
    assert worker_children, (
        "Expected the knowledge subgraph to expand under "
        "retrieve_worker_node at xray=1 "
        f"(saw nodes: {sorted(nodes)})"
    )


def test_compiled_graph_cross_product_both_flags_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flags on: chat + knowledge subgraphs coexist under xray."""
    agent = _build_agent(
        use_knowledge_subgraph=True,
        monkeypatch=monkeypatch,
        use_chat_subgraph=True,
    )
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
