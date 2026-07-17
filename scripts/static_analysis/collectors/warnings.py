# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Collect pytest warning filters from configuration and decorators."""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from ..fingerprints import containing_symbol
from ..model import Finding, Mechanism, TargetKind
from .helpers import FindingParts, make_finding


class _WarningEntry(TypedDict):
    """One pytest filter with its source location metadata."""

    tool: str
    rule: str
    key: str
    symbol: str | None
    mechanism: Mechanism
    source: str


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _finding(root: Path, path: Path, entry: _WarningEntry) -> Finding:
    display_path = _relative_path(root, path)
    symbol = entry["symbol"] or entry["key"]
    parts: FindingParts = {
        "tool": entry["tool"],
        "rule": entry["rule"],
        "mechanism": entry["mechanism"],
        "target_kind": (
            TargetKind.FIXTURE if entry["symbol"] else TargetKind.CONFIG
        ),
        "path": display_path,
        "symbol": symbol,
        "peer_path": None,
        "peer_symbol": None,
        "location": f"{display_path}:{entry['key']}",
        "message": entry["source"],
        "source": entry["source"],
    }
    return make_finding(parts)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _string_argument(node: ast.Call) -> str | None:
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    value = node.args[0].value
    return value if isinstance(value, str) else None


def _config_filters(root: Path) -> list[Finding]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    values = (
        document.get("tool", {})
        .get("pytest", {})
        .get("ini_options", {})
        .get("filterwarnings", [])
    )
    if not isinstance(values, list):
        return []
    findings: list[Finding] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            continue
        key = f"tool.pytest.ini_options.filterwarnings[{index}]"
        findings.append(
            _finding(
                root,
                path,
                {
                    "tool": "pytest",
                    "rule": value,
                    "key": key,
                    "symbol": None,
                    "mechanism": Mechanism.CONFIG,
                    "source": value,
                },
            )
        )
    return findings


def _decorator_filters(root: Path, path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        symbol = containing_symbol(source, node.lineno)
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if _dotted_name(decorator.func) != "pytest.mark.filterwarnings":
                continue
            value = _string_argument(decorator)
            if value is None:
                continue
            key = f"{symbol or node.name}.pytest.mark.filterwarnings"
            findings.append(
                _finding(
                    root,
                    path,
                    {
                        "tool": "pytest",
                        "rule": value,
                        "key": key,
                        "symbol": symbol,
                        "mechanism": Mechanism.DECORATOR,
                        "source": value,
                    },
                )
            )
    return findings


def collect_warning_suppressions(
    root: Path, paths: Sequence[Path]
) -> tuple[Finding, ...]:
    """Collect pytest warning filters and filterwarnings decorators."""
    findings = _config_filters(root)
    for path in sorted(paths, key=lambda item: _relative_path(root, item)):
        if path.suffix == ".py":
            findings.extend(_decorator_filters(root, path))
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.path, item.symbol or "", item.rule),
        )
    )
