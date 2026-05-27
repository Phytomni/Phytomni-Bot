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
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Env install must precede any mcp_server_phytomni import. Mirrors the
# pattern in the repo-root ``conftest.py``; see that file's docstring
# for why this lives at module scope rather than in a function called
# from ``main()``. E402 below is accepted for the same reason.
_FAKE_ENV = {
    "PHYTOMNI_TESTING": "1",
    "DOMAIN_NAME": "viz-domain",
    "USER_NAME": "viz-user",
    "USER_PASSWORD": "viz-password",
    "ACCESS_KEY_ID": "viz-access-key-id",
    "SECRET_ACCESS_KEY": "viz-secret-access-key",
    "BASE_URL": "https://example.invalid/llm",
    "MODEL_ID": "viz-model",
    "API_KEY": "viz-api-key",
    "CODER_URL": "https://example.invalid/coder",
    "CODER_MODEL": "viz-coder-model",
    "CODER_API_KEY": "viz-coder-api-key",
    "EMBED_URL": "https://example.invalid/embed",
    "EMBED_MODEL": "viz-embed-model",
    "EMBED_API_KEY": "viz-embed-api-key",
    "BI_TOKEN": "viz-bi-token",
    "RETRIEVE_URL": "https://example.invalid/retrieve",
    "RERANK_URL": "https://example.invalid/rerank",
    "CREATE_TASK_URL": "https://example.invalid/create-task",
    "UPDATE_TASK_URL": "https://example.invalid/update-task",
    "SPA_FAQ_URL": "https://example.invalid/repos/{repo_id}/faqs",
    "REPO_ID": "viz-repo-id",
    "REPO_ID_DICT": '{"viz-repo-id": 128}',
    "WORKSPACE_ID": "viz-workspace-id",
    "SUBJECT_ID": "viz-subject-id",
    "DATA_REPO_ID": "viz-data-repo-id",
    "TOOL_REPO_ID": "viz-tool-repo-id",
    "PROTOCOL_REPO_ID": "viz-protocol-repo-id",
    "SPA_REPO_ID": "viz-spa-repo-id",
    "DATABASE_URL": "https://example.invalid/database",
    "ANALYSIS_URL": "https://example.invalid/analysis",
    "BI_URL": "https://example.invalid/bi",
}
for _name, _value in _FAKE_ENV.items():
    os.environ.setdefault(_name, _value)

# pylint: disable=wrong-import-position
from mcp_server_phytomni.agents.analyst.agent import (  # noqa: E402
    AnalystAgent,
)
from mcp_server_phytomni.agents.brief_gene.core import (  # noqa: E402
    BriefGeneAgent,
)
from mcp_server_phytomni.agents.data.agent import (  # noqa: E402
    DataAgent,
)
from mcp_server_phytomni.agents.deep_genome.agent import (  # noqa: E402
    DeepGenomeAgents,
)
from mcp_server_phytomni.agents.knowledge.agent import (  # noqa: E402
    KnowledgeAgent,
)
from mcp_server_phytomni.graphs import (  # noqa: E402
    SubgraphRegistry,
    SubgraphSpec,
    export_manifest,
)

# pylint: enable=wrong-import-position


def _brief_gene_factory() -> Any:
    """Factory returning a compiled BriefGeneAgent app."""
    return BriefGeneAgent().app


def _deep_genome_factory() -> Any:
    """Factory returning a compiled DeepGenomeAgents app with default deps."""
    return DeepGenomeAgents(
        data_agent=DataAgent(),
        knowledge_agent=KnowledgeAgent(),
        analyst_agent=AnalystAgent(),
    ).app


def build_default_registry() -> SubgraphRegistry:
    """Return the Phase-0 registry: only agents already compileable today.

    Additional agents (chat / knowledge / data / review / analyst /
    research / design / network / environment / evolution) are
    registered by their respective Phase as those Phases land. This
    Phase-0 baseline ships brief_gene and deep_genome only because
    those two already construct cleanly without subgraph composition.
    """
    registry = SubgraphRegistry()
    registry.register(
        SubgraphSpec(id="brief_gene", factory=_brief_gene_factory)
    )
    registry.register(
        SubgraphSpec(id="deep_genome", factory=_deep_genome_factory)
    )
    return registry


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
