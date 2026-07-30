#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Render registered Phytomni LangGraph apps for visualization.

Default run prints Mermaid source for every registered subgraph to
stdout. ``--png DIR`` writes PNG via LangGraph's built-in
``draw_mermaid_png`` which posts the node/edge metadata to the public
``mermaid.ink`` renderer. ``--manifest DIR`` exports a JSON manifest
per graph. ``--list`` enumerates registered ids without compiling.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

# The ``_visualize_bootstrap`` module installs the offline fake
# deployment env on its own import and then re-exports the three
# mcp subgraph symbols this script needs. Routing both through the
# bootstrap module keeps every import in this file a real ``from``
# import — no side-effect-only import block, no suppression of the
# unused-import warning that pattern requires. Static type checking uses
# the package-qualified name, while direct invocation keeps the sibling
# import resolved from ``sys.path[0]``. The CLI test inserts that same path
# before ``spec.loader.exec_module`` so the bootstrap is resolvable in both
# runtime modes.
if TYPE_CHECKING:
    from scripts._visualize_bootstrap import (
        build_default_registry,
        export_manifest,
    )
else:
    from _visualize_bootstrap import (
        build_default_registry,
        export_manifest,
    )


def _disambiguate_subgraph_leaves(graph: Any) -> Any:
    """Give every nested subgraph occurrence a globally unique leaf name.

    LangGraph mounts one shared ``chat`` subgraph at several nesting
    positions — knowledge's generate node and its retrieve worker each
    embed it, and analyst / brief_gene add their own chat node on top.
    ``draw_mermaid`` deduplicates subgraphs by the leaf segment of their
    node-id path across the whole diagram, so two ``chat`` occurrences
    raise ``ValueError`` once ``xray`` expands more than one. This pass
    suffixes only the colliding *container* segments (``chat`` ->
    ``chat_2`` ...) in node ids and edge endpoints, leaving terminal
    node names (``__start__`` / ``llm_node`` ...) untouched, so the
    render reflects the real reused composition instead of failing. A
    new graph is returned only when a leaf collides; otherwise the
    original graph is handed back so non-nested renders stay identical.
    """
    containers: set[str] = set()
    for node_id in graph.nodes:
        parts = node_id.split(":")
        for depth in range(1, len(parts)):
            containers.add(":".join(parts[:depth]))

    used: set[str] = set()
    renamed: dict[str, str] = {}
    for prefix in sorted(containers, key=lambda p: (p.count(":"), p)):
        leaf = prefix.rsplit(":", maxsplit=1)[-1]
        candidate, counter = leaf, 2
        while candidate in used:
            candidate = f"{leaf}_{counter}"
            counter += 1
        used.add(candidate)
        renamed[prefix] = candidate

    if all(
        renamed[prefix] == prefix.rsplit(":", maxsplit=1)[-1]
        for prefix in containers
    ):
        return graph

    def _remap(path: str) -> str:
        walked: list[str] = []
        out: list[str] = []
        for segment in path.split(":"):
            walked.append(segment)
            out.append(renamed.get(":".join(walked), segment))
        return ":".join(out)

    new_ids = {node_id: _remap(node_id) for node_id in graph.nodes}
    new_nodes = {
        new_ids[node_id]: node.copy(id=new_ids[node_id])
        for node_id, node in graph.nodes.items()
    }
    new_edges = [
        edge.copy(source=_remap(edge.source), target=_remap(edge.target))
        for edge in graph.edges
    ]
    return type(graph)(nodes=new_nodes, edges=new_edges)


def _print_mermaid(name: str, graph_app: Any, xray: int) -> None:
    """Print a labeled Mermaid block for one agent's compiled graph."""
    graph = _disambiguate_subgraph_leaves(graph_app.get_graph(xray=xray))
    print(f"\n=== {name} ===")
    print("```mermaid")
    print(graph.draw_mermaid().rstrip())
    print("```")


def _write_png(
    name: str,
    graph_app: Any,
    out_dir: Path,
    xray: int,
) -> Path:
    """Write one agent's graph as a PNG file under ``out_dir``."""
    graph = _disambiguate_subgraph_leaves(graph_app.get_graph(xray=xray))
    target = out_dir / f"{name}.png"
    target.write_bytes(graph.draw_mermaid_png())
    return target


def _write_manifest(name: str, graph_app: Any, out_dir: Path) -> Path:
    """Write one agent's graph manifest as JSON under ``out_dir``."""
    manifest = export_manifest(graph_app)
    target = out_dir / f"{name}.json"
    target.write_text(
        json.dumps(manifest.model_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _select_names(
    registry: Any,
    selected: str | None,
) -> tuple[str, ...]:
    """Return selected ids, or every registered id when omitted."""
    if selected is None:
        return registry.names()
    if selected not in registry.names():
        print(f"unknown graph: {selected}", file=sys.stderr)
        return ()
    return (selected,)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the script's CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        default=None,
        metavar="NAME",
        help="Render only one registered subgraph id.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List registered subgraph ids and exit without compiling.",
    )
    parser.add_argument(
        "--xray",
        type=int,
        default=1,
        metavar="DEPTH",
        help="Forwarded to LangGraph get_graph(xray=DEPTH).",
    )
    parser.add_argument(
        "--png",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Also write PNG files to DIR using mermaid.ink. "
            "Directory is created if missing."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Also write JSON graph manifest files to DIR. "
            "Directory is created if missing."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render registered graphs as Mermaid and optional artifacts."""
    args = _build_arg_parser().parse_args(argv)
    registry = build_default_registry()
    if args.list:
        for name in registry.names():
            print(name)
        return 0

    names = _select_names(registry, args.agent)
    if not names:
        return 2

    if args.png is not None:
        args.png.mkdir(parents=True, exist_ok=True)
    if args.manifest is not None:
        args.manifest.mkdir(parents=True, exist_ok=True)

    for name in names:
        graph_app = registry.get_or_compile(name)
        _print_mermaid(name, graph_app, args.xray)
        if args.png is not None:
            written = _write_png(name, graph_app, args.png, args.xray)
            print(f"[png] wrote {written}", file=sys.stderr)
        if args.manifest is not None:
            written = _write_manifest(name, graph_app, args.manifest)
            print(f"[manifest] wrote {written}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
