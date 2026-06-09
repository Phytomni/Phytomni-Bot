# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Allowed subgraph identifiers for the declarative graph loader.

Derived from ``build_default_registry().names()`` so the loader's
``subgraph_ref`` safety check shares one source of truth with the
visualization script, manifest exports, and the project test
matrix. Scope today is restricted to subgraph-id lookup;
``node_ref`` / ``route_fn`` resolution belongs to later phases.
"""

from __future__ import annotations

from .defaults import build_default_registry


def default_subgraph_allowlist() -> frozenset[str]:
    """Return the frozen set of subgraph ids accepted by the loader.

    The set is derived once per call from
    ``build_default_registry().names()`` so updates to the registered
    subgraph catalog (a new agent landing in Phase 8+ or a removal in
    a hygiene push) flow into the loader's safety check without a
    second source of truth to keep in sync.

    Returns:
        Frozenset of every subgraph id registered in the default
        ``SubgraphRegistry``. Order is irrelevant because callers
        only do membership tests; the registry's ``names()`` already
        returns alphabetically-sorted ids so a deterministic dump is
        available when needed.
    """
    return frozenset(build_default_registry().names())


__all__ = ["default_subgraph_allowlist"]
