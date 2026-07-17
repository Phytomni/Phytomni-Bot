# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the scoped static-analysis gate contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_MAKEFILE = (_ROOT / "Makefile").read_text(encoding="utf-8")
_GATE = (_ROOT / "scripts/scoped_gate.sh").read_text(encoding="utf-8")
_FULL_GATE = (_ROOT / "scripts/validate_local.sh").read_text(encoding="utf-8")
_WORKFLOW = (_ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
_CHECKS = _GATE.split(
    "# Static-analysis exemption reconciliation.", maxsplit=1
)[1].split("# Static + format check", maxsplit=1)[0]


def _command(scope: str) -> str:
    return (
        rf"run uv run python scripts/check_static_analysis_exemptions\.py"
        rf"\s*\\\s*check --scope {scope}"
    )


def test_python_changes_run_one_cross_file_check() -> None:
    """A Python change invokes the exact cross-file checker once."""
    assert len(re.findall(_command("cross-file"), _CHECKS)) == 1
    assert 'if [ -z "$py_files" ]; then' in _CHECKS
    assert (
        "no changed .py files; skipping static-analysis cross-file check"
        in (_CHECKS)
    )


def test_non_python_changes_skip_cross_file_check() -> None:
    """The empty Python partition has an explicit skip branch."""
    match = re.search(
        r'if \[ -z "\$py_files" \]; then(?P<body>.*?)else(?P<run>.*?)\nfi',
        _CHECKS,
        flags=re.DOTALL,
    )
    assert match is not None
    assert "cross-file check" in match.group("body")
    assert re.search(_command("cross-file"), match.group("run"))


def test_policy_paths_force_full_inventory() -> None:
    """Policy/tooling paths select the full deny-by-default inventory."""
    for path in (
        "static-analysis-exemptions.toml",
        "scripts/static_analysis/*",
        "scripts/check_static_analysis_exemptions.py",
        "pyproject.toml",
        ".flake8",
        ".github/workflows/*",
    ):
        assert path in _GATE
    assert "policy_changed=0" in _GATE
    assert '[ "$policy_changed" -eq 0 ]' in _CHECKS
    assert len(re.findall(_command("full"), _CHECKS)) == 1


def test_full_gate_checks_registry_and_generated_ledger() -> None:
    """The full local gate uses the shared checker and renderer."""
    assert "render-docs --check" in _FULL_GATE
    assert re.search(_command("full"), _FULL_GATE)
    assert "scripts/check_pylint_baseline.py" in _FULL_GATE
    assert "reconcile(" not in _FULL_GATE


def test_local_gate_entrypoints_share_the_generated_checker() -> None:
    """Make targets and local gates converge on one checker implementation."""
    assert "scripts/scoped_gate.sh scoped" in _MAKEFILE
    assert "scripts/validate_local.sh" in _MAKEFILE
    for gate in (_GATE, _FULL_GATE):
        assert "scripts/check_static_analysis_exemptions.py" in gate
    assert "static-analysis-exemptions.toml" in _GATE


def test_ci_static_exemptions_job_matches_local_policy() -> None:
    """CI has a clean full-policy job without legacy bypasses."""
    assert "\n  static-exemptions:" in _WORKFLOW
    job = _WORKFLOW.split("\n  static-exemptions:", 1)[1].split(
        "\n  pytest:", 1
    )[0]
    assert 'python-version: "3.12"' in job
    assert 'uv pip install --system -e ".[dev,demo]"' in job
    assert "scripts/check_static_analysis_exemptions.py" in job
    assert "check --scope full" in job
    assert "render-docs --check" in job
    assert "--registry" not in job
    assert "scripts/check_pylint_baseline.py" not in job
    assert "--disable=R0801,R0903" not in job
