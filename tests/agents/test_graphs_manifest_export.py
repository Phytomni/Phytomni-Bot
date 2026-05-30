# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for ``mcp_server_phytomni.graphs.manifest.export_manifest``.

The tests build minimal LangGraph apps in-process (no agent imports)
to validate the (node / edge / kind) contract, then exercise one
real agent (``BriefGeneAgent``) to confirm the exporter handles the
project's actual compiled graphs without modification.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.chat.builder import _build_chat_graph
from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.graphs.manifest import (
    GraphManifest,
    GraphNodeManifest,
    export_manifest,
)


class _SimpleState(TypedDict):
    value: int


class _SubState(TypedDict):
    value: int


def _identity(state: _SimpleState) -> _SimpleState:
    return state


def _build_linear_app():
    """Return a compiled START -> a -> b -> END linear graph."""
    workflow = StateGraph(_SimpleState)
    workflow.add_node("a", _identity)
    workflow.add_node("b", _identity)
    workflow.add_edge(START, "a")
    workflow.add_edge("a", "b")
    workflow.add_edge("b", END)
    return workflow.compile()


def _build_conditional_app():
    """Return a compiled graph with one conditional router."""

    def _router(_state: _SimpleState) -> str:
        return "a"

    workflow = StateGraph(_SimpleState)
    workflow.add_node("a", _identity)
    workflow.add_node("b", _identity)
    workflow.add_conditional_edges(START, _router, {"a": "a", "b": "b"})
    workflow.add_edge("a", END)
    workflow.add_edge("b", END)
    return workflow.compile()


def _build_parent_with_subgraph():
    """Return a parent app embedding a compiled child as ``"child"``."""
    sub = StateGraph(_SubState)
    sub.add_node("inner", _identity)
    sub.add_edge(START, "inner")
    sub.add_edge("inner", END)
    sub_app = sub.compile()

    parent = StateGraph(_SimpleState)
    parent.add_node("p", _identity)
    parent.add_node("child", sub_app)
    parent.add_edge(START, "p")
    parent.add_edge("p", "child")
    parent.add_edge("child", END)
    return parent.compile()


def test_export_linear_app_lists_every_top_level_node() -> None:
    """``export_manifest`` reflects all top-level nodes from a linear graph."""
    manifest = export_manifest(_build_linear_app())

    assert isinstance(manifest, GraphManifest)
    names = {node.name for node in manifest.nodes}
    assert {"a", "b", "__start__", "__end__"} <= names


def test_export_linear_app_marks_boundary_kinds() -> None:
    """Boundary ``__start__`` / ``__end__`` nodes are classified separately."""
    manifest = export_manifest(_build_linear_app())
    by_name = {node.name: node for node in manifest.nodes}

    assert by_name["__start__"].kind == "boundary"
    assert by_name["__end__"].kind == "boundary"
    assert by_name["a"].kind == "node"
    assert by_name["b"].kind == "node"


def test_export_linear_app_preserves_edges() -> None:
    """All edges including START -> a survive the export."""
    manifest = export_manifest(_build_linear_app())
    edge_pairs = {(edge.source, edge.target) for edge in manifest.edges}

    assert ("__start__", "a") in edge_pairs
    assert ("a", "b") in edge_pairs
    assert ("b", "__end__") in edge_pairs


def test_export_conditional_router_marks_conditional_flag() -> None:
    """At least one edge for a conditional router is ``conditional=True``."""
    manifest = export_manifest(_build_conditional_app())
    assert any(edge.conditional for edge in manifest.edges)


def test_export_subgraph_node_kind_is_subgraph() -> None:
    """A node mounted with ``add_node(name, compiled_app)`` is detected."""
    manifest = export_manifest(_build_parent_with_subgraph())
    by_name = {node.name: node for node in manifest.nodes}

    assert by_name["child"].kind == "subgraph"
    assert by_name["p"].kind == "node"


def test_subgraph_node_names_property_lists_only_subgraphs() -> None:
    """``subgraph_node_names`` excludes plain nodes and boundaries."""
    manifest = export_manifest(_build_parent_with_subgraph())
    assert manifest.subgraph_node_names == ("child",)


def test_manifest_is_frozen() -> None:
    """``GraphManifest`` rejects mutation by construction.

    ``setattr`` reaches the same ``__setattr__`` path as a literal
    ``manifest.nodes = ()`` assignment (Pydantic raises
    ``ValidationError`` for frozen models there); the dynamic form
    lets static checkers see an API call instead of a forbidden
    field write, removing the need for ``# type: ignore[misc]``.
    Same refactor as the GeneRetrieveRequest frozen test at
    ``9072103``.
    """
    manifest = export_manifest(_build_linear_app())
    with __import__("pytest").raises(Exception):
        setattr(manifest, "nodes", ())


def test_node_manifest_validates_non_empty_name() -> None:
    """``GraphNodeManifest`` rejects empty names at construction time."""
    with __import__("pytest").raises(Exception):
        GraphNodeManifest(name="")


def test_export_real_brief_gene_agent_node_set() -> None:
    """Real BriefGeneAgent export matches the documented five-node graph."""
    manifest = export_manifest(BriefGeneAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "query_judge_node",
        "fetch_annotation_node",
        "retrieve_node",
        "generate_node",
        "follow_up_node",
    }
    assert documented <= names


def test_export_real_chat_subgraph_node_set() -> None:
    """Real chat subgraph export matches the documented three-node graph.

    Pins the chat subgraph's manifest shape so the docs section in
    ``docs/agent-graphs.md`` and the compiled graph stay in sync. If
    a future refactor renames a node or collapses the prepare /
    generate split, the assertion fails first and the docs update
    rides in the same diff.
    """
    manifest = export_manifest(_build_chat_graph())
    names = {node.name for node in manifest.nodes}
    documented = {
        "prepare_context_node",
        "generate_node",
        "follow_up_node",
    }
    assert documented <= names


def test_export_real_knowledge_subgraph_node_set() -> None:
    """Real KnowledgeAgent export matches the documented four-node graph.

    Pins the knowledge subgraph's manifest shape so the docs section
    in ``docs/agent-graphs.md`` and the compiled graph stay in sync.
    """
    manifest = export_manifest(KnowledgeAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "process_files_node",
        "retrieve_node",
        "generate_node",
        "follow_up_node",
    }
    assert documented <= names


def test_export_real_data_subgraph_node_set() -> None:
    """Real DataAgent export matches the documented three-node graph.

    Pins the data subgraph's manifest shape so the docs section in
    ``docs/agent-graphs.md`` and the compiled graph stay in sync.
    """
    manifest = export_manifest(DataAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "retrieve_node",
        "rewrite_node",
        "search_node",
    }
    assert documented <= names


def test_export_real_analyst_subgraph_node_set() -> None:
    """Real AnalystAgent export matches the documented nine-node graph.

    Pins the analyst subgraph's manifest shape so the docs section
    in ``docs/agent-graphs.md`` and the compiled graph stay in
    sync. A future refactor that collapses one of the routing
    nodes surfaces here before the manifest JSON snapshot diverges.
    """
    manifest = export_manifest(AnalystAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "parse_query_node",
        "data_select_node",
        "method_retrieve_node",
        "plan_node",
        "check_node",
        "tool_extract_node",
        "tool_retrieve_node",
        "submit_node",
        "pooling_node",
    }
    assert documented <= names


def test_export_real_review_subgraph_node_set() -> None:
    """Real DeepResearchAgent export matches the documented seven-node graph.

    Pins the review subgraph's manifest shape so the docs section
    in ``docs/agent-graphs.md`` and the compiled graph stay in
    sync. A future refactor that drops one of the pipeline stages
    surfaces here before the manifest JSON snapshot diverges.
    """
    manifest = export_manifest(DeepResearchAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "plan_node",
        "retrieve_node",
        "draft_node",
        "review_node",
        "revise_node",
        "summary_node",
        "post_process_node",
    }
    assert documented <= names
