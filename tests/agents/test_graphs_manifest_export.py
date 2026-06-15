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
from mcp_server_phytomni.agents.environment.builder import (
    build_environment_graph,
)
from mcp_server_phytomni.agents.evolution.builder import (
    build_evolution_graph,
)
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.graphs.defaults import _build_deep_genome_app
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
    """Real BriefGeneAgent export covers the preamble + knowledge topology.

    A parallel annotation / homology fetch feeds four section nodes that
    fan into the introduction and pure-template render; the retrieve site
    mounts the Send-dispatch knowledge triad (prep_tasks → worker × N →
    reduce); the trailing follow_up site splits into a prep + post pair
    around the shared ``chat`` mount.
    """
    manifest = export_manifest(BriefGeneAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "query_judge_node",
        "fetch_annotation_node",
        "fetch_homology_interactions_node",
        "retrieve_prep_tasks_node",
        "retrieve_worker_node",
        "retrieve_reduce_node",
        "section_discovery_node",
        "section_cloning_node",
        "section_functional_node",
        "section_application_node",
        "introduction_node",
        "render_node",
        "follow_up_prep_node",
        "follow_up_post_node",
        "chat",
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
    """Real KnowledgeAgent export matches the structural-mount topology.

    Pins the knowledge subgraph's manifest shape so the docs section
    in ``docs/agent-graphs.md`` and the compiled graph stay in sync.
    The legacy single-node ``generate_node`` / ``follow_up_node``
    bodies were retired in favor of the structural chat mount; the
    compiled graph now always wires the prep + chat + post split.
    """
    manifest = export_manifest(KnowledgeAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "process_files_node",
        "retrieve_node",
        "generate_prep_node",
        "generate_post_node",
        "follow_up_prep_node",
        "follow_up_post_node",
        "chat",
    }
    assert documented <= names


def test_export_real_data_subgraph_node_set() -> None:
    """Real DataAgent export matches the structural-mount topology.

    Pins the data subgraph's manifest shape so the docs section in
    ``docs/agent-graphs.md`` and the compiled graph stay in sync.
    The legacy single-node ``rewrite_node`` body was retired in
    favor of the structural chat mount; ``retrieve_node`` also splits
    into prep + post around the mounted knowledge subgraph.
    """
    manifest = export_manifest(DataAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "retrieve_prep_node",
        "retrieve_post_node",
        "rewrite_prep_node",
        "rewrite_post_node",
        "knowledge",
        "chat",
        "search_node",
    }
    assert documented <= names


def test_export_real_analyst_subgraph_node_set() -> None:
    """Real AnalystAgent export covers the chat + knowledge node set.

    Pins the analyst subgraph's structural-mount shape so the docs
    section in ``docs/agent-graphs.md`` and the compiled graph stay
    in sync. Each chat site becomes a prep + post pair around the
    shared ``chat`` node; the method_retrieve site becomes a prep +
    post pair around the mounted ``knowledge`` node.
    """
    manifest = export_manifest(AnalystAgent().app)
    names = {node.name for node in manifest.nodes}
    documented = {
        "parse_query_prep_node",
        "parse_query_post_node",
        "data_select_prep_node",
        "data_select_post_node",
        "method_retrieve_prep_node",
        "method_retrieve_post_node",
        "knowledge",
        "plan_prep_node",
        "plan_post_node",
        "check_prep_node",
        "check_post_node",
        "tool_extract_prep_node",
        "tool_extract_post_node",
        "chat",
        "tool_retrieve_node",
        "submit_node",
        "pooling_node",
    }
    assert documented <= names


def test_export_real_review_subgraph_node_set() -> None:
    """Real DeepResearchAgent export covers every compiled-graph node.

    Cross-checks ``export_manifest`` against the agent's own
    ``app.get_graph().nodes`` so a future export-helper bug that
    silently drops one of the seven pipeline stages
    (``plan_node`` -> ... -> ``post_process_node``) surfaces here.
    The documented node-name set is pinned canonically by
    ``test_review_graph_io.test_review_subgraph_node_set``; this
    test only confirms the export reflects what compile produced.
    """
    agent = DeepResearchAgent()
    manifest = export_manifest(agent.app)
    names = {node.name for node in manifest.nodes}
    compiled = {n.id for n in agent.app.get_graph().nodes.values()}
    assert compiled <= names


def test_export_real_environment_subgraph_node_set() -> None:
    """Real environment subgraph export matches the two-node graph.

    Pins the environment subgraph's manifest shape so the docs
    section in ``docs/agent-graphs.md`` and the compiled graph
    stay in sync. The documented node-name set is canonically
    pinned by ``test_environment_graph`` two-node assertion.
    """
    manifest = export_manifest(build_environment_graph())
    names = {node.name for node in manifest.nodes}
    documented = {
        "extract_region_codes_node",
        "submit_vci_task_node",
    }
    assert documented <= names


def test_export_real_evolution_subgraph_node_set() -> None:
    """Real evolution subgraph export matches the two-node graph.

    Pins the evolution subgraph's manifest shape so the docs
    section in ``docs/agent-graphs.md`` and the compiled graph
    stay in sync. The documented node-name set is canonically
    pinned by ``test_evolution_graph`` two-node assertion.
    """
    manifest = export_manifest(build_evolution_graph())
    names = {node.name for node in manifest.nodes}
    documented = {
        "resolve_target_taxids_node",
        "submit_evolution_task_node",
    }
    assert documented <= names


def test_export_real_deep_genome_subgraph_node_set() -> None:
    """Real deep_genome subgraph export matches the documented set.

    Pins the deep_genome subgraph's manifest shape so the docs
    section in ``docs/agent-graphs.md`` and the compiled graph
    stay in sync. The documented node-name set covers the three
    phases (Part 1 brief_gene preamble + analyst fan-out / Part 2
    synthesis + experiment loop / Part 3 protocol → discussion →
    summary → follow-up) that the report mixin walks.
    """
    manifest = export_manifest(_build_deep_genome_app())
    names = {node.name for node in manifest.nodes}
    documented = {
        "brief_gene_node",
        "prepare_tasks_node",
        "gene_expression_tissues_node",
        "gene_expression_cultivars_node",
        "gene_expression_treatments_node",
        "gene_expression_genotypes_node",
        "single_cell_node",
        "promoter_node",
        "smep_node",
        "smoc_node",
        "protein_structure_node",
        "evolution_node",
        "design_node",
        "synthesize_node",
        "experiment_node",
        "protocol_node",
        "discussion_node",
        "summary_node",
        "follow_up_node",
    }
    assert documented <= names
