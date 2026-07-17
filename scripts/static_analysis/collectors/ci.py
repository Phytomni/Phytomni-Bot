# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Collect static-analysis disables embedded in CI and gate commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from ..model import Finding, Mechanism, TargetKind
from .helpers import FindingParts, make_finding


class _CiEntry(TypedDict):
    """One suppressive CI or gate command occurrence."""

    line_number: int
    line: str
    tool: str
    rule: str
    symbol: str


_FLAG_RE = re.compile(
    r"(?P<flag>--(?:disable|ignore|extend-ignore))"
    r"(?:=|\s+)(?P<rules>[A-Za-z0-9_*, -]+?)(?=\s|$)"
)
_BASELINE_RE = re.compile(r"check_pylint_baseline\.py")


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _tool_for_line(line: str) -> str:
    lowered = line.lower()
    for tool in ("pylint", "ruff", "flake8", "mypy", "pyright"):
        if tool in lowered:
            return tool
    return "gate"


def _finding(root: Path, path: Path, entry: _CiEntry) -> Finding:
    display_path = _relative_path(root, path)
    source = entry["line"].strip()
    parts: FindingParts = {
        "tool": entry["tool"],
        "rule": entry["rule"],
        "mechanism": Mechanism.COMMAND,
        "target_kind": TargetKind.COMMAND,
        "path": display_path,
        "symbol": entry["symbol"],
        "peer_path": None,
        "peer_symbol": None,
        "location": f"{display_path}:{entry['line_number']}",
        "message": source,
        "source": source,
    }
    return make_finding(parts)


def _entry(
    line_number: int, line: str, tool: str, rule: str, symbol: str
) -> _CiEntry:
    return {
        "line_number": line_number,
        "line": line,
        "tool": tool,
        "rule": rule,
        "symbol": symbol,
    }


def _candidate_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    workflows = root / ".github" / "workflows"
    if workflows.exists():
        paths.extend(sorted(workflows.glob("*.y*ml")))
    paths.extend(
        path
        for path in (
            root / "scripts" / "validate_local.sh",
            root / "scripts" / "scoped_gate.sh",
            root / "Makefile",
        )
        if path.exists()
    )
    return tuple(paths)


def collect_ci_suppressions(root: Path) -> tuple[Finding, ...]:
    """Collect exact command-line suppressions from gate and CI files."""
    findings: list[Finding] = []
    for path in _candidate_paths(root):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            tool = _tool_for_line(line)
            for match in _FLAG_RE.finditer(line):
                values = tuple(
                    item.strip()
                    for item in match.group("rules").split(",")
                    if item.strip()
                )
                for rule in values:
                    findings.append(
                        _finding(
                            root,
                            path,
                            _entry(
                                line_number,
                                line,
                                tool,
                                rule,
                                match.group("flag"),
                            ),
                        )
                    )
            if _BASELINE_RE.search(line):
                findings.append(
                    _finding(
                        root,
                        path,
                        _entry(
                            line_number,
                            line,
                            "pylint",
                            "numeric-baseline",
                            "check_pylint_baseline.py",
                        ),
                    )
                )
    return tuple(
        sorted(
            findings, key=lambda item: (item.path, item.location, item.rule)
        )
    )
