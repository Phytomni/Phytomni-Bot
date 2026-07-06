#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Enforce per-rule pylint baselines for cross-file rules without per-file
knobs.

Some pylint rules — notably R0801 ``duplicate-code`` (which spans two or
more files) and R0903 ``too-few-public-methods`` (which fires on test
fake classes scattered across many files) — cannot be silenced with a
file-level ``# pylint: disable=...`` in the way the bracketed
function-level disables in the rest of the codebase do. Project-wide
disables would let new violations slip in silently. This script
instead enforces a ratchet: each rule has a fixed expected violation
count; pylint output above the count fails the gate, output below the
count emits a "ratchet down" hint so the baseline can be tightened in
the same diff as a refactor.

The script is intentionally narrow. It only handles rules listed in
``RULE_BASELINES``; everything else flows through normal pylint
exit-code reporting upstream of this script.

Exit codes:
- ``0`` — every tracked rule equals its baseline.
- ``1`` — at least one tracked rule exceeded its baseline OR pylint
  invocation itself failed.
- ``2`` — pylint binary not found / unusable.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Per-rule baseline counts. Each entry locks the current measured
# violation count so a regression above it fails the local gate; a
# refactor that lowers the count emits a "ratchet down" hint that the
# author should apply in the same diff. The rationale for each rule
# stays exempt-here-instead-of-refactored lives in
# ``docs/lint-exemptions.md``.
RULE_BASELINES: dict[str, int] = {
    "R0801": 92,  # duplicate-code: cross-file similar blocks
    # too-few-public-methods: test fake classes + Protocol stubs +
    # GraphLoader (load + flag-gated __init__ only) + the terminal-result
    # assembly seams (TerminalAnswerContext DTO, ArtifactLister /
    # AnswerSynthesizer single-method Protocols) + the brief_gene preamble
    # fan-in test stub (_StubKnowledgeApp). Pinned at the count measured
    # under the installed pylint 4.0.x.
    "R0903": 20,
}


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=("Enforce per-rule pylint baselines for cross-file rules.")
    )
    parser.add_argument(
        "--pylint-cmd",
        default="uv run pylint --persistent=no",
        help=(
            "Pylint invocation prefix (default: %(default)s). The script "
            "appends the tracked file list."
        ),
    )
    parser.add_argument(
        "--files-cmd",
        default="git ls-files *.py",
        help=(
            "Command that lists the .py files to scan, one per line "
            "(default: %(default)s)."
        ),
    )
    return parser.parse_args()


def _list_files(files_cmd: str) -> list[str]:
    """Resolve the tracked .py file list via ``files_cmd``."""
    result = subprocess.run(
        shlex.split(files_cmd),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"check_pylint_baseline: files command failed: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)
    return [line for line in result.stdout.splitlines() if line]


def _run_pylint(pylint_cmd: str, files: Iterable[str]) -> str:
    """Run pylint over ``files`` and return its stdout."""
    cmd = [*shlex.split(pylint_cmd), *files]
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        print(
            f"check_pylint_baseline: pylint not found: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    return result.stdout


def _count_violations(
    pylint_output: str,
) -> dict[str, int]:
    """Count occurrences of each rule code in pylint output."""
    counts: dict[str, int] = {rule: 0 for rule in RULE_BASELINES}
    for line in pylint_output.splitlines():
        for rule in RULE_BASELINES:
            if f": {rule}:" in line:
                counts[rule] += 1
    return counts


def _evaluate(counts: dict[str, int]) -> tuple[list[str], list[str]]:
    """Return (violations, ratchet_hints) by comparing counts to floors."""
    violations: list[str] = []
    hints: list[str] = []
    for rule, baseline in RULE_BASELINES.items():
        actual = counts[rule]
        if actual > baseline:
            violations.append(
                f"  {rule}: {actual} > {baseline} baseline "
                f"(+{actual - baseline})"
            )
        elif actual < baseline:
            hints.append(
                f"  {rule}: {actual} < {baseline} baseline; "
                f"lower the entry to {actual} in this diff"
            )
    return violations, hints


def main() -> int:
    """Entrypoint."""
    args = _parse_args()
    files = _list_files(args.files_cmd)
    if not files:
        print(
            "check_pylint_baseline: no .py files matched the files "
            "command; skipping baseline check.",
            file=sys.stderr,
        )
        return 0
    output = _run_pylint(args.pylint_cmd, files)
    counts = _count_violations(output)
    violations, hints = _evaluate(counts)

    if violations:
        print(
            "check_pylint_baseline: rule count above baseline",
            file=sys.stderr,
        )
        for line in violations:
            print(line, file=sys.stderr)
        if hints:
            print("hints:", file=sys.stderr)
            for line in hints:
                print(line, file=sys.stderr)
        return 1

    if hints:
        print("check_pylint_baseline: ratchet-down hints")
        for line in hints:
            print(line)
        return 0

    summary = ", ".join(f"{rule}={counts[rule]}" for rule in RULE_BASELINES)
    print(f"check_pylint_baseline: at baseline ({summary}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
