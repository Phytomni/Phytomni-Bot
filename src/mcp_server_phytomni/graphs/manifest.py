# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Read-only graph manifest exported from compiled LangGraph apps.

``export_manifest(compiled_app)`` reflects a compiled top-level graph
into a Pydantic structure containing node names (with subgraph
detection) and edges (with conditional / target metadata). The
manifest is the static view used by visualization tooling and CI
drift checks; runtime loading of declarative manifests is not in
scope here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .architecture import GRAPH_ARCHITECTURE

_BOUNDARY_NODES = frozenset({"__start__", "__end__"})


class GraphNodeManifest(BaseModel):
    """Serializable metadata for one graph node."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    kind: Literal["node", "subgraph", "boundary"] = "node"


class GraphEdgeManifest(BaseModel):
    """Serializable metadata for one graph edge."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    target: str | None = Field(default=None, min_length=1)
    conditional: bool = False


class GraphManifest(BaseModel):
    """Serializable structural manifest of one compiled LangGraph app."""

    model_config = ConfigDict(frozen=True)

    graph_id: str | None = None
    classification: Literal["public", "internal", "unspecified"] = (
        "unspecified"
    )
    public_agent: str | None = None
    lifecycle: tuple[str, ...] = ()
    subgraph_dependencies: tuple[str, ...] = ()
    remote_providers: tuple[str, ...] = ()
    driver: str | None = None
    topology: str | None = None
    nodes: tuple[GraphNodeManifest, ...] = ()
    edges: tuple[GraphEdgeManifest, ...] = ()

    @property
    def subgraph_node_names(self) -> tuple[str, ...]:
        """Return the names of nodes whose body is another compiled graph."""
        return tuple(
            node.name for node in self.nodes if node.kind == "subgraph"
        )


def _node_kind(
    node_data: Any,
    name: str,
) -> Literal["node", "subgraph", "boundary"]:
    """Classify a LangGraph node by its underlying ``data`` payload."""
    if name in _BOUNDARY_NODES:
        return "boundary"
    # LangGraph attaches the compiled child graph as ``node.data`` when
    # a subgraph is mounted via ``parent.add_node(name, child_app)``.
    # Duck-typing on ``get_graph`` is too loose because every Runnable
    # exposes that method; the class name is the cheapest stable
    # distinguishing signal that does not pull ``CompiledStateGraph``
    # into our import graph.
    if type(node_data).__name__ == "CompiledStateGraph":
        return "subgraph"
    return "node"


def export_manifest(
    compiled_app: Any, *, graph_id: str | None = None
) -> GraphManifest:
    """Return a :class:`GraphManifest` reflecting a compiled LangGraph app.

    Args:
        compiled_app: Compiled LangGraph application
            (the ``workflow.compile()`` result). Must expose
            ``get_graph()`` returning an object with ``.nodes`` (mapping
            of name to ``Node``) and ``.edges`` (iterable of ``Edge``
            with ``source`` / ``target`` / ``conditional`` attributes).

    Returns:
        ``GraphManifest`` carrying the names of every top-level node
        (with subgraph nodes marked ``kind="subgraph"``) and every
        edge (with the ``conditional`` flag preserved).
    """
    raw_graph = compiled_app.get_graph()
    nodes: list[GraphNodeManifest] = []
    for name, node in raw_graph.nodes.items():
        node_data = getattr(node, "data", None)
        nodes.append(
            GraphNodeManifest(name=name, kind=_node_kind(node_data, name))
        )
    edges: list[GraphEdgeManifest] = []
    for edge in raw_graph.edges:
        edges.append(
            GraphEdgeManifest(
                source=edge.source,
                target=getattr(edge, "target", None),
                conditional=bool(getattr(edge, "conditional", False)),
            )
        )
    architecture = GRAPH_ARCHITECTURE.get(graph_id or "")
    return GraphManifest(
        graph_id=graph_id,
        classification=(
            architecture.classification if architecture else "unspecified"
        ),
        public_agent=architecture.public_agent if architecture else None,
        lifecycle=architecture.lifecycle if architecture else (),
        subgraph_dependencies=(
            architecture.subgraph_dependencies if architecture else ()
        ),
        remote_providers=(
            architecture.remote_providers if architecture else ()
        ),
        driver=architecture.driver if architecture else None,
        topology=architecture.topology if architecture else None,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )
