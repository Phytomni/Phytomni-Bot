#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compile explicitly approved local gene materials; never publish them.

The pure contract is loaded by file rather than importing the application
package, whose bootstrap loads deployment credentials even for submodules.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Build or check a local bundle, emitting only safe result metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--source-root", required=True, type=Path)
    build.add_argument("--output-root", required=True, type=Path)
    check = commands.add_parser("check")
    check.add_argument("--bundle-root", required=True, type=Path)
    check.add_argument("--gene-id", required=True)
    args = parser.parse_args(argv)
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "mcp_server_phytomni"
        / "storage"
        / "gene_examples.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_curated_gene_offline", path
    )
    assert spec is not None and spec.loader is not None
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)
    try:
        if args.command == "build":
            with args.input.open("rb") as source:
                raw = source.read(contract.MAX_MANIFEST_BYTES + 1)
            manifest = contract.build_gene_bundle(
                raw, args.source_root, args.output_root
            )
        else:
            manifest = contract.check_gene_bundle(
                args.bundle_root, args.gene_id
            )
    except contract.CuratedGeneError as exc:
        print(f"Curated bundle rejected: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("Curated bundle rejected: input_unavailable", file=sys.stderr)
        return 1
    print(
        f"Validated local bundle: {manifest.reference_count} reference slots, "
        f"{len(manifest.resources)} resources; no publication performed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
