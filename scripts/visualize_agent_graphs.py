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
from typing import Any

# Side-effect import: installs offline fake env BEFORE the
# ``mcp_server_phytomni`` imports below. Lives in a dedicated
# bootstrap module so the rest of this file can keep every
# ``from mcp_server_phytomni...`` import at the top of the file
# (no ``E402`` / ``wrong-import-position`` exception needed). The
# ``W0611`` / ``F401`` suppression pair marks the import as
# intentionally side-effect-only — the equivalent pattern is the
# repo-root ``conftest.py`` which is loaded by pytest before any
# test module parses its imports. ``scripts/`` is on ``sys.path[0]``
# when the script runs via ``python scripts/visualize_agent_graphs.py``;
# the CLI test in ``tests/agents/test_visualize_agent_graphs_cli.py``
# adds the directory itself before calling
# ``spec.loader.exec_module``.
import _visualize_bootstrap  # noqa: F401  pylint: disable=unused-import

from mcp_server_phytomni.graphs import SubgraphRegistry, export_manifest
from mcp_server_phytomni.graphs.defaults import build_default_registry


def _print_mermaid(name: str, graph_app: Any, xray: int) -> None:
    """Print a labeled Mermaid block for one agent's compiled graph."""
    graph = graph_app.get_graph(xray=xray)
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
    graph = graph_app.get_graph(xray=xray)
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
    registry: SubgraphRegistry,
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
