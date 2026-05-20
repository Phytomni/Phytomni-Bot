#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Normalize JSON config files with deterministic formatting.

This script exposes `run_json_normalizer` as the CLI entrypoint. It also uses
private helpers to collect project config JSON files and rewrite each file
with sorted keys, two-space indentation, and a trailing newline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CONFIG_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "mcp_server_phytomni"
    / "config"
)
JSON_INDENT = 2


def _collect_config_json_files() -> list[Path]:
    """Return config JSON files in deterministic order."""
    return sorted(CONFIG_DIR.glob("*.json"))


def _normalized_text(file_path: Path) -> str:
    """Return the canonical normalized text for one JSON file."""
    with file_path.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            indent=JSON_INDENT,
            sort_keys=True,
        )
        + "\n"
    )


def _rewrite_json_file(file_path: Path) -> None:
    """Normalize one JSON file in place."""
    file_path.write_text(_normalized_text(file_path), encoding="utf-8")


def _check_json_file(file_path: Path) -> bool:
    """Return True when the file already matches its normalized form."""
    return file_path.read_text(encoding="utf-8") == _normalized_text(file_path)


def _parse_cli_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Normalize JSON files with sorted keys and two-space indent."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify each file matches its normalized form. "
            "Exit 1 on any drift; never rewrite. "
            "Intended for the local and CI quality gates."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="JSON file paths to normalize. Defaults to config/*.json.",
    )
    return parser.parse_args()


def run_json_normalizer() -> None:
    """Normalize requested JSON files, or all config JSON files by default.

    Returns:
        None. The selected JSON files are rewritten in place unless
        ``--check`` is given, in which case the script exits 1 on any
        file that does not already match its normalized form.
    """
    args = _parse_cli_args()
    file_paths: list[Path] = args.paths or _collect_config_json_files()

    if args.check:
        drifted = [p for p in file_paths if not _check_json_file(p)]
        if drifted:
            for path in drifted:
                print(f"json drift: {path}")
            raise SystemExit(1)
        return

    for file_path in file_paths:
        _rewrite_json_file(file_path)


if __name__ == "__main__":
    run_json_normalizer()
