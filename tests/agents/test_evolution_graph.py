# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the evolution LangGraph structural assembly.

Pins the two-node set (``resolve_target_taxids_node`` and
``submit_evolution_task_node``), the START edge, the conditional
edge after resolution with explicit ``path_map`` over both
branches, and the ``input_schema`` / ``output_schema`` /
``state_schema`` wiring that exposes a narrow public contract to
parent graphs.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.evolution.builder import (
    build_evolution_graph,
)
from mcp_server_phytomni.agents.evolution.graph import route_after_resolve
from mcp_server_phytomni.agents.evolution.state import (
    EvolutionInput,
    EvolutionOutput,
    EvolutionState,
)

pytestmark = pytest.mark.agent


def test_build_evolution_graph_compiles_with_two_nodes() -> None:
    """The compiled graph carries exactly two non-boundary nodes.

    Pins the resolve / submit split called out by the plan. If a
    future refactor inlines resolution into submission (or splits
    one of them further), this test fails first so the rebalance
    is intentional and any manifest snapshot / visualization
    rendering can be updated in the same diff.
    """
    app = build_evolution_graph()
    nodes = set(app.get_graph().nodes)
    non_boundary = nodes - {"__start__", "__end__"}
    assert non_boundary == {
        "resolve_target_taxids_node",
        "submit_evolution_task_node",
    }


def test_evolution_graph_start_edge_targets_resolve() -> None:
    """``__start__`` flows into ``resolve_target_taxids_node`` first.

    Pins ordering: taxonomy resolution must happen before the
    analyst submission, otherwise ``submit_evolution_task_node``
    would have no taxids to fill the goal prompt with and the
    analyst submission would receive a malformed template.
    """
    app = build_evolution_graph()
    edges = app.get_graph().edges
    start_targets = {e.target for e in edges if e.source == "__start__"}
    assert start_targets == {"resolve_target_taxids_node"}


def test_evolution_graph_conditional_edge_after_resolve() -> None:
    """``resolve_target_taxids_node`` branches to submit or ``__end__``.

    Pins the conditional edge with an explicit ``path_map`` over
    both branches. Without an explicit path_map, the LangGraph
    Mermaid renderer cannot label the branches by name — the
    failure-vs-happy split collapses into an anonymous fork that
    hides which paths exist.
    """
    app = build_evolution_graph()
    resolve_edges = [
        e
        for e in app.get_graph().edges
        if e.source == "resolve_target_taxids_node"
    ]
    targets = {e.target for e in resolve_edges}
    conditional_flags = {e.conditional for e in resolve_edges}
    assert "submit_evolution_task_node" in targets
    assert "__end__" in targets
    assert conditional_flags == {True}


def test_evolution_graph_submit_edges_into_end() -> None:
    """``submit_evolution_task_node`` is terminal.

    Pins that the analyst submission is the last step on its
    branch; no extra node sneaks in after submission. A future
    polling or status-check node would replace this edge and the
    test would fail so the new topology is intentional.
    """
    app = build_evolution_graph()
    submit_edges = [
        e
        for e in app.get_graph().edges
        if e.source == "submit_evolution_task_node"
    ]
    assert len(submit_edges) == 1
    assert submit_edges[0].target == "__end__"


def test_evolution_graph_exposes_input_and_output_schemas() -> None:
    """The compiled graph carries narrow input / output schemas.

    Pins that a parent graph mounting this subgraph sees
    ``EvolutionInput`` fields on the input port and
    ``EvolutionOutput`` fields on the output port — the full
    ``EvolutionState`` (including the intermediate
    ``target_taxids``) is hidden from parents.
    """
    app = build_evolution_graph()
    assert app.builder.input_schema is EvolutionInput
    assert app.builder.output_schema is EvolutionOutput
    assert app.builder.state_schema is EvolutionState


def test_route_after_resolve_routes_to_submit_when_taxids_present() -> None:
    """A populated ``target_taxids`` string routes to submission.

    Pins the happy path: when extraction yielded any taxids (the
    ``"All"`` sentinel or a comma-joined list), the analyst
    submission still runs. The wrapper's legacy semantics
    submitted whatever taxids extraction produced; this test pins
    the graph reproduces that behavior.
    """
    state: EvolutionState = {
        "query": "anything",
        "species_code": "osa",
        "gene_id": "AtPHYB",
        "target_taxids": "9606,10090",
    }
    assert route_after_resolve(state) == "submit_evolution_task_node"


def test_route_after_resolve_short_circuits_when_taxids_none() -> None:
    """A ``None`` target_taxids value short-circuits to ``__end__``.

    Pins the failure path: when chat extraction returned ``None``
    (legacy wrapper's early-return), the analyst submission is
    skipped and the wrapper output carries
    ``{"evolution_agents_task": None}``.
    """
    state: EvolutionState = {
        "query": "ambiguous request",
        "species_code": "osa",
        "gene_id": "AtPHYB",
        "target_taxids": None,
    }
    assert route_after_resolve(state) == "__end__"
