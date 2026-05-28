# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``mcp_server_phytomni.graphs.defaults``.

Pins the central registration site for the project's built-in
subgraphs: ``build_default_registry`` ships at least brief_gene /
chat / deep_genome, the names are returned in a deterministic order,
and each registered id resolves to a callable factory the registry
can compile through ``get_or_compile``.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.graphs import SubgraphRegistry, SubgraphSpec
from mcp_server_phytomni.graphs.defaults import build_default_registry

pytestmark = pytest.mark.agent


def test_build_default_registry_returns_subgraph_registry() -> None:
    """``build_default_registry`` returns a populated SubgraphRegistry.

    Pins the constructor contract: callers can rely on the returned
    object being a real :class:`SubgraphRegistry`, not a tuple or
    dict, so they can pass it straight to
    ``SubgraphRegistry.get_or_compile`` without an adapter shim.
    """
    registry = build_default_registry()
    assert isinstance(registry, SubgraphRegistry)


def test_default_registry_ships_phase_one_baseline() -> None:
    """The default set covers brief_gene / chat / deep_genome.

    Pins the Phase-1 baseline that downstream callers (the
    visualization script, future loaders, integration tests) expect.
    If a future refactor drops one of the three from defaults, this
    test fails first so the rebalance is explicit.
    """
    registry = build_default_registry()
    assert {"brief_gene", "chat", "deep_genome"} <= set(registry.names())


def test_default_registry_names_are_deterministic_alphabetical() -> None:
    """``registry.names()`` returns ids in sorted order.

    Pins the deterministic enumeration contract :class:`SubgraphRegistry`
    promises: tests, manifests, and visualization output that loop
    over the names list want a stable order so snapshots and diffs
    do not flicker between runs.
    """
    registry = build_default_registry()
    names = registry.names()
    assert list(names) == sorted(names)


def test_default_registry_specs_carry_factories() -> None:
    """Every registered spec exposes a callable factory.

    Pins that the registry is wired with real factories, not
    placeholder ``None`` entries. Without this, the first call to
    ``get_or_compile`` for any id would raise a ``TypeError`` deep
    inside the cache rather than failing fast at construction time.
    """
    registry = build_default_registry()
    for name in registry.names():
        spec = registry.get_spec(name)
        assert isinstance(spec, SubgraphSpec)
        assert callable(spec.factory)


def test_default_registry_compiles_chat_subgraph() -> None:
    """``get_or_compile("chat")`` returns a compiled chat app.

    Pins that the chat factory produces a real LangGraph-compiled
    app exposing the ``get_graph`` introspection seam the
    visualization layer drives. Tests for the other built-ins
    (brief_gene, deep_genome) are covered by their own agent test
    suites — checking one factory here is enough to lock the
    integration shape without re-running the whole chat workflow.
    """
    registry = build_default_registry()
    chat_app = registry.get_or_compile("chat")
    graph = chat_app.get_graph()
    assert "generate_node" in graph.nodes
    assert "prepare_context_node" in graph.nodes
    assert "follow_up_node" in graph.nodes
