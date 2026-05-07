#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Normalize JSON files: sort keys and format with stable indentation."""

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


def _rewrite_json_file(file_path: Path) -> None:
    """Normalize one JSON file in place."""
    with file_path.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=JSON_INDENT,
            sort_keys=True,
        )
        f.write("\n")


def _parse_cli_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Normalize JSON files with sorted keys and two-space indent."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="JSON file paths to normalize. Defaults to config/*.json.",
    )
    return parser.parse_args()


def run_json_normalizer() -> None:
    """Normalize requested JSON files, or all config JSON files by default."""
    args = _parse_cli_args()
    file_paths: list[Path] = args.paths or _collect_config_json_files()

    for file_path in file_paths:
        _rewrite_json_file(file_path)


if __name__ == "__main__":
    run_json_normalizer()
