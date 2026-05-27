# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Graph composition primitives for cross-loaded LangGraph subgraphs.

This package provides the registry that lets one compiled LangGraph
subgraph be loaded as a node inside another agent's parent graph (the
``parent.add_node("name", child_compiled_app)`` pattern), together with
the spec object describing each subgraph's identity, factory, and
public input/output contract.
"""

from .adapters import adapter_node
from .manifest import (
    GraphEdgeManifest,
    GraphManifest,
    GraphNodeManifest,
    export_manifest,
)
from .registry import SubgraphRegistry
from .spec import SubgraphSpec

__all__ = [
    "GraphEdgeManifest",
    "GraphManifest",
    "GraphNodeManifest",
    "SubgraphRegistry",
    "SubgraphSpec",
    "adapter_node",
    "export_manifest",
]
