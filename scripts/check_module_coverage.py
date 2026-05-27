#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Enforce per-module coverage floors for ``src/mcp_server_phytomni/agents/``.

The pyproject ``[tool.coverage.report].fail_under`` knob enforces a single
global floor but does not let us pin individual agent files. This script
reads the JSON coverage report (default: ``coverage.json`` produced by
``pytest --cov-report=json:coverage.json``) and checks each
``agents/*`` file against a ratchet floor: files listed in
``MODULE_FLOORS`` must stay at or above their pinned percentage; every
other agent file must reach ``TARGET`` (80%). Each push that lifts an
agent module raises its entry toward ``TARGET`` so the floor never
drifts back down.

Exit codes:
- ``0`` — all agent files meet their floor.
- ``1`` — at least one agent file is below its floor.
- ``2`` — ``coverage.json`` is missing or malformed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERAGE_JSON = PROJECT_ROOT / "coverage.json"
AGENT_PATH_PREFIX = "src/mcp_server_phytomni/agents/"

# Per-file floors. Each entry locks the current measured percentage so a
# regression below it fails the local gate. Each ratchet push raises one
# entry toward ``TARGET`` alongside the new tests that support the bump.
MODULE_FLOORS: Dict[str, int] = {
    "src/mcp_server_phytomni/agents/deep_genome/report.py": 23,
    "src/mcp_server_phytomni/agents/analyst/graph.py": 42,
    "src/mcp_server_phytomni/agents/review/report.py": 42,
    "src/mcp_server_phytomni/agents/knowledge/agent.py": 44,
    "src/mcp_server_phytomni/agents/deep_genome/profile.py": 47,
    "src/mcp_server_phytomni/agents/brief_gene/core.py": 55,
    "src/mcp_server_phytomni/agents/deep_genome/dispatch.py": 58,
    "src/mcp_server_phytomni/agents/review/planning.py": 58,
    "src/mcp_server_phytomni/agents/analyst/core.py": 64,
    "src/mcp_server_phytomni/agents/analyst/task_ops.py": 70,
    "src/mcp_server_phytomni/agents/design/agent.py": 74,
    "src/mcp_server_phytomni/agents/shared/analysis_storage.py": 76,
}
TARGET = 80


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


def _load_coverage(path: Path) -> Dict[str, dict]:
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


def _evaluate(
    files: Dict[str, dict],
) -> tuple[int, List[str]]:
    """Return (checked_count, violations) for all agent files."""
    violations: List[str] = []
    checked = 0
    for path in sorted(files):
        if not path.startswith(AGENT_PATH_PREFIX):
            continue
        pct = float(files[path]["summary"]["percent_covered"])
        floor = MODULE_FLOORS.get(path, TARGET)
        checked += 1
        if pct < floor:
            violations.append(f"  {path}: {pct:.2f}% < {floor}% floor")
    return checked, violations


def main() -> int:
    """Entrypoint."""
    args = _parse_args()
    files = _load_coverage(args.coverage_json)
    checked, violations = _evaluate(files)
    if violations:
        print(
            f"check_module_coverage: "
            f"{len(violations)} module(s) below floor",
            file=sys.stderr,
        )
        for line in violations:
            print(line, file=sys.stderr)
        return 1
    print(
        f"check_module_coverage: all {checked} agents/* files at or "
        f"above their floors (target {TARGET}%)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
