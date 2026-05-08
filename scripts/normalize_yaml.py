#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Manage YAML config files with hierarchical path operations.

This script exposes `manage_yaml_path` for writing nested prompt paths and
`sort_yaml_content` for deterministic sorting. Private helpers normalize
line wrapping, scalar quoting, and dumper behavior for stable YAML output.
"""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path
from typing import Any

import yaml

LINE_WIDTH = 72


class _LiteralString(str):
    """String value rendered as a YAML literal block scalar."""


class _QuotedString(str):
    """String value rendered as a single-quoted YAML scalar."""


class _NormalizedDumper(yaml.SafeDumper):
    """YAML dumper for stable, lint-friendly config output."""

    def increase_indent(self, flow=False, indentless=False):
        """Indent sequence items under their parent key."""
        return super().increase_indent(flow, False)


def _literal_representer(
    dumper: yaml.SafeDumper, data: _LiteralString
) -> yaml.nodes.ScalarNode:
    """Represent multiline string values with explicit line breaks."""
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(data), style="|"
    )


def _quoted_representer(
    dumper: yaml.SafeDumper, data: _QuotedString
) -> yaml.nodes.ScalarNode:
    """Represent single-line string values with one quoting style."""
    return dumper.__class__.represent_scalar(
        dumper, "tag:yaml.org,2002:str", str(data), style="'"
    )


_NormalizedDumper.add_representer(_LiteralString, _literal_representer)
_NormalizedDumper.add_representer(_QuotedString, _quoted_representer)


def manage_yaml_path(file_path: str, yaml_path: str, prompt_text: str) -> None:
    """Write or update a nested path in a YAML file with sorting.

    Args:
        file_path: Path to the YAML file.
        yaml_path: Slash-separated hierarchical path (e.g., "system/ai4ps").
        prompt_text: Content to write at the specified path.

    Returns:
        None. The YAML file is created or rewritten in place.
    """
    keys = yaml_path.split("/")
    target_key = keys[-1]
    parent_keys = keys[:-1]

    yaml_file = Path(file_path)
    data: dict[str, Any] = {}
    if yaml_file.exists():
        data = _read_yaml(yaml_file)

    current = data
    for key in parent_keys:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]

    current[target_key] = prompt_text

    _write_yaml(yaml_file, _sort_and_process_recursive(data))


def sort_yaml_content(file_path: str) -> None:
    """Sort YAML file content hierarchically and unify line-ending format.

    Args:
        file_path: Path to the YAML file.

    Returns:
        None. Existing YAML mappings are rewritten in place.
    """
    yaml_file = Path(file_path)
    if not yaml_file.exists():
        return

    data = _read_yaml(yaml_file)

    if not data:
        return

    _write_yaml(yaml_file, _sort_and_process_recursive(data))


def _read_yaml(file_path: Path) -> dict[str, Any]:
    """Read a YAML file into memory as text, then parse it."""
    with file_path.open("r", encoding="utf-8") as f:
        raw_content = f.read()

    data = yaml.safe_load(raw_content) or {}
    if not isinstance(data, dict):
        raise TypeError(
            f"{file_path} must contain a YAML mapping at top level"
        )
    return data


def _write_yaml(file_path: Path, data: dict[str, Any]) -> None:
    """Write processed YAML data with deterministic formatting."""
    with file_path.open("w", encoding="utf-8") as f:
        yaml.dump(
            _prepare_for_dump(data),
            f,
            allow_unicode=True,
            default_flow_style=False,
            Dumper=_NormalizedDumper,
            explicit_start=True,
            sort_keys=False,
            width=LINE_WIDTH,
        )


def _sort_and_process_recursive(obj: Any) -> Any:
    """Recursively sort dict keys and normalize string line endings.

    Args:
        obj: Object to process (dict, list, str, or other).

    Returns:
        Processed object with sorted keys and normalized line endings.
    """
    if isinstance(obj, dict):
        return {
            k: _sort_and_process_recursive(v)
            for k, v in sorted(obj.items(), key=lambda item: str(item[0]))
        }
    if isinstance(obj, list):
        return [_sort_and_process_recursive(item) for item in obj]
    if isinstance(obj, str):
        return _normalize_string(obj)
    return obj


def _normalize_string(value: str) -> str:
    """Normalize escaped line endings, trailing spaces, and long lines."""
    processed = value.replace("\r\n", "\n").replace("\r", "\n")
    processed = processed.replace("\\n", "\n")
    processed = processed.replace("\\ \\ ", " ")
    processed = processed.replace("\\\n", " ")

    lines = [line.rstrip() for line in processed.split("\n")]
    return "\n".join(_wrap_line(line) for line in lines).rstrip("\n")


def _wrap_line(line: str) -> str:
    """Wrap long prompt text lines while preserving indentation."""
    if len(line) <= LINE_WIDTH:
        return line

    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    wrapped = textwrap.wrap(
        stripped,
        width=max(20, LINE_WIDTH - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
    )
    return "\n".join(f"{indent}{item}" for item in wrapped)


def _prepare_for_dump(obj: Any) -> Any:
    """Wrap only string values, so mapping keys keep normal YAML style."""
    if isinstance(obj, dict):
        return {k: _prepare_for_dump(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_prepare_for_dump(item) for item in obj]
    if isinstance(obj, str):
        if "\n" in obj:
            return _LiteralString(obj)
        return _QuotedString(obj)
    return obj


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manage and normalize YAML files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Set a nested YAML path")
    set_parser.add_argument("file_path", help="YAML file path")
    set_parser.add_argument(
        "yaml_path", help="Slash-separated path (e.g. system/ai4ps)"
    )
    set_parser.add_argument("prompt_text", help="Content to write")

    sort_parser = subparsers.add_parser("sort", help="Sort YAML file content")
    sort_parser.add_argument("file_path", help="YAML file path")

    args = parser.parse_args()

    if args.command == "set":
        manage_yaml_path(args.file_path, args.yaml_path, args.prompt_text)
    elif args.command == "sort":
        sort_yaml_content(args.file_path)
