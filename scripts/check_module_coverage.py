#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Enforce 80% branch-enabled coverage for packaged production Python.

The pyproject ``[tool.coverage.report].fail_under`` knob enforces a
single global floor. This script reads the JSON coverage report
(default: ``coverage.json`` produced by
``pytest --cov-report=json:coverage.json``) and fail-closes on every
tracked file under ``src/mcp_server_phytomni/`` and
``src/mcp_client_phytomni/``. Zero-statement files are inventoried but
are not violations. Missing files and any ``percent_covered`` below
``TARGET`` fail the gate.

Exit codes:
- ``0`` — every executable production file meets ``TARGET``.
- ``1`` — at least one production file is missing or below ``TARGET``.
- ``2`` — ``coverage.json`` is missing or malformed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERAGE_JSON = PROJECT_ROOT / "coverage.json"
SOURCE_PREFIXES = (
    "src/mcp_server_phytomni/",
    "src/mcp_client_phytomni/",
)
TARGET = 80


@dataclass(frozen=True)
class CoverageResult:
    """Production inventory evaluation for the uniform 80% gate."""

    checked: int
    missing: list[str]
    violations: list[str]
    zero_statement: list[str]


_DOC_FIRST_LINE = (__doc__ or "").splitlines()[0]


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=_DOC_FIRST_LINE)
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=DEFAULT_COVERAGE_JSON,
        help="Path to coverage.json (default: <repo>/coverage.json).",
    )
    return parser.parse_args()


def _load_coverage(path: Path) -> dict[str, dict]:
    """Load ``files`` table from coverage.json."""
    if not path.exists():
        print(
            f"check_module_coverage: {path} not found; "
            "run pytest with --cov-report=json:coverage.json first",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"check_module_coverage: {path} is not valid JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    files = data.get("files")
    if not isinstance(files, dict):
        print(
            f"check_module_coverage: {path} has no 'files' table",
            file=sys.stderr,
        )
        sys.exit(2)
    return files


def _tracked_production_files() -> set[str]:
    """Return tracked Python files under the two production prefixes."""
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "src/mcp_server_phytomni",
            "src/mcp_client_phytomni",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked: set[str] = set()
    for raw in completed.stdout.splitlines():
        path = raw.replace("\\", "/")
        if not path.endswith(".py"):
            continue
        if path.startswith(SOURCE_PREFIXES):
            tracked.add(path)
    return tracked


def _evaluate_production(
    files: dict[str, dict], expected: set[str]
) -> CoverageResult:
    """Evaluate every tracked production file against ``TARGET``."""
    missing = sorted(expected - set(files))
    violations: list[str] = []
    zero_statement: list[str] = []
    checked = 0
    for path in sorted(expected):
        record = files.get(path)
        if record is None:
            continue
        summary = record.get("summary")
        if not isinstance(summary, dict) or "percent_covered" not in summary:
            violations.append(path)
            continue
        statements = int(summary.get("num_statements") or 0)
        if statements == 0:
            zero_statement.append(path)
            continue
        checked += 1
        if float(summary["percent_covered"]) < TARGET:
            violations.append(path)
    return CoverageResult(
        checked=checked,
        missing=missing,
        violations=violations,
        zero_statement=zero_statement,
    )


def main() -> int:
    """Entrypoint."""
    args = _parse_args()
    files = _load_coverage(args.coverage_json)
    result = _evaluate_production(files, _tracked_production_files())
    if result.missing:
        print(
            "check_module_coverage: "
            f"{len(result.missing)} production file(s) missing from report",
            file=sys.stderr,
        )
        for path in result.missing:
            print(f"  missing {path}", file=sys.stderr)
        return 1
    if result.violations:
        print(
            "check_module_coverage: "
            f"{len(result.violations)} module(s) below {TARGET}%",
            file=sys.stderr,
        )
        for path in result.violations:
            summary = files.get(path, {}).get("summary", {})
            pct = summary.get("percent_covered", 0)
            print(f"  {path}: {float(pct):.2f}% < {TARGET}%", file=sys.stderr)
        return 1
    zero = ""
    if result.zero_statement:
        zero = f" ({len(result.zero_statement)} zero-statement inventoried)"
    print(
        f"check_module_coverage: all {result.checked} production files "
        f"at or above {TARGET}%.{zero}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
