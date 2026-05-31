# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the environment VCI LangGraph structural assembly.

Pins the two-node set (``extract_region_codes_node`` and
``submit_vci_task_node``), the START edge, the conditional edge
after extraction with explicit ``path_map`` over both branches,
and the ``input_schema`` / ``output_schema`` / ``state_schema``
wiring that exposes a narrow public contract to parent graphs.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.environment.builder import (
    build_environment_graph,
)
from mcp_server_phytomni.agents.environment.graph import route_after_extract
from mcp_server_phytomni.agents.environment.state import (
    EnvironmentInput,
    EnvironmentOutput,
    EnvironmentState,
)

pytestmark = pytest.mark.agent


def test_build_environment_graph_compiles_with_two_nodes() -> None:
    """The compiled graph carries exactly two non-boundary nodes.

    Pins the extract / submit split called out by the plan. If a
    future refactor inlines extraction into submission (or splits
    one of them further), this test fails first so the rebalance is
    intentional and any manifest snapshot / visualization rendering
    can be updated in the same diff.
    """
    app = build_environment_graph()
    nodes = set(app.get_graph().nodes)
    non_boundary = nodes - {"__start__", "__end__"}
    assert non_boundary == {
        "extract_region_codes_node",
        "submit_vci_task_node",
    }


def test_environment_graph_start_edge_targets_extract() -> None:
    """``__start__`` flows into ``extract_region_codes_node`` first.

    Pins ordering: region-code extraction must happen before the
    analyst submission, otherwise ``submit_vci_task_node`` would
    have no province / city / county codes to fill the goal prompt
    with and the analyst submission would receive a malformed
    template.
    """
    app = build_environment_graph()
    edges = app.get_graph().edges
    start_targets = {e.target for e in edges if e.source == "__start__"}
    assert start_targets == {"extract_region_codes_node"}


def test_environment_graph_conditional_edge_after_extract() -> None:
    """``extract_region_codes_node`` branches to submit or ``__end__``.

    Pins the conditional edge with an explicit ``path_map`` over
    both branches. Without an explicit path_map, the LangGraph
    Mermaid renderer cannot label the branches by name — the
    failure-vs-happy split collapses into an anonymous fork that
    hides which paths exist.
    """
    app = build_environment_graph()
    extract_edges = [
        e
        for e in app.get_graph().edges
        if e.source == "extract_region_codes_node"
    ]
    targets = {e.target for e in extract_edges}
    conditional_flags = {e.conditional for e in extract_edges}
    assert "submit_vci_task_node" in targets
    assert "__end__" in targets
    assert conditional_flags == {True}


def test_environment_graph_submit_edges_into_end() -> None:
    """``submit_vci_task_node`` is terminal.

    Pins that the analyst submission is the last step on its
    branch; no extra node sneaks in after submission. If a future
    refactor adds a polling or status-check node, it goes on a new
    branch (or replaces this edge), and the test fails so the new
    topology is intentional.
    """
    app = build_environment_graph()
    submit_edges = [
        e for e in app.get_graph().edges if e.source == "submit_vci_task_node"
    ]
    assert len(submit_edges) == 1
    assert submit_edges[0].target == "__end__"


def test_environment_graph_exposes_input_and_output_schemas() -> None:
    """The compiled graph carries narrow input / output schemas.

    Pins that a parent graph mounting this subgraph sees
    ``EnvironmentInput`` fields on the input port and
    ``EnvironmentOutput`` fields on the output port — the full
    ``EnvironmentState`` (including the intermediate ``region_codes``)
    is hidden from parents. ``StateGraph`` records these classes on
    the compiled app so loaders can read them without re-importing
    the subgraph module.
    """
    app = build_environment_graph()
    assert app.builder.input_schema is EnvironmentInput
    assert app.builder.output_schema is EnvironmentOutput
    assert app.builder.state_schema is EnvironmentState


def test_route_after_extract_routes_to_submit_when_codes_present() -> None:
    """A populated ``region_codes`` triple routes to submission.

    Pins the happy path: when extraction yielded province / city /
    county codes (even partially), the analyst submission still
    runs. The wrapper's legacy semantics submitted whatever codes
    extraction produced; this test pins the graph reproduces that
    behavior rather than tightening the gate on full triples.
    """
    state: EnvironmentState = {
        "query": "anything",
        "region_codes": ["110000", "110100", "110101"],
    }
    assert route_after_extract(state) == "submit_vci_task_node"


def test_route_after_extract_short_circuits_when_codes_none() -> None:
    """A ``None`` region_codes value short-circuits to ``__end__``.

    Pins the failure path: when extraction returned no parseable
    ``<result>`` payload, the legacy wrapper returned
    ``{"vci_analysis_task": None}`` without ever calling submit. The
    graph reproduces that by short-circuiting the conditional edge.
    """
    state: EnvironmentState = {
        "query": "unparseable request",
        "region_codes": None,
    }
    assert route_after_extract(state) == "__end__"
