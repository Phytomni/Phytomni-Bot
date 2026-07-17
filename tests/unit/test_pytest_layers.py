# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for repository pytest layer conventions."""

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

EXPECTED_LAYER_DIRS = {
    "unit": "unit",
    "server": "server",
    "agents": "agent",
    "integration": "integration",
}
EXPECTED_MARKER_PREFIXES = {
    f"{marker}:"
    for marker in [
        "unit",
        "server",
        "agent",
        "integration",
        "network",
    ]
}


def _project_root() -> Path:
    """Verify project root."""
    return Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    """Verify pyproject."""
    return tomllib.loads(
        (_project_root() / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_pytest_default_run_is_offline_and_strict():
    """Verify pytest default run is offline and strict."""
    pytest_options = _pyproject()["tool"]["pytest"]["ini_options"]

    assert pytest_options["testpaths"] == ["tests"]
    assert pytest_options["pythonpath"] == ["src"]
    assert "--strict-config" in pytest_options["addopts"]
    assert "--strict-markers" in pytest_options["addopts"]
    assert "not integration and not network" in pytest_options["addopts"]


def test_pytest_layer_markers_are_registered():
    """Verify pytest layer markers are registered."""
    markers = set(_pyproject()["tool"]["pytest"]["ini_options"]["markers"])
    marker_prefixes = {marker.split(" ", maxsplit=1)[0] for marker in markers}

    assert marker_prefixes >= EXPECTED_MARKER_PREFIXES


def test_test_files_live_in_named_pytest_layers():
    """Verify test files live in named pytest layers."""
    tests_root = _project_root() / "tests"
    bad_paths = [
        path.relative_to(tests_root).as_posix()
        for path in tests_root.rglob("test_*.py")
        if path.relative_to(tests_root).parts[0] not in EXPECTED_LAYER_DIRS
    ]

    assert bad_paths == []


def test_pytest_coverage_reporting_is_configured_for_ci():
    """Verify pytest coverage reporting is configured for ci."""
    pyproject = _pyproject()
    optional_dev = pyproject["project"]["optional-dependencies"]["dev"]
    coverage_config = pyproject["tool"]["coverage"]

    assert any(dep.startswith("pytest-cov") for dep in optional_dev)
    assert "dependency-groups" not in pyproject
    assert coverage_config["run"] == {
        "branch": True,
        "relative_files": True,
        "source": ["mcp_server_phytomni", "mcp_client_phytomni"],
    }
    assert coverage_config["report"] == {
        "fail_under": 80,
        "show_missing": True,
        "skip_covered": False,
        "exclude_also": [
            "if TYPE_CHECKING:",
            "if __name__ == .__main__.:",
            "raise NotImplementedError",
        ],
    }
    assert coverage_config["xml"] == {"output": "coverage.xml"}


def test_ci_pytest_job_writes_coverage_report():
    """Verify ci pytest job writes coverage report."""
    workflow = (_project_root() / ".github/workflows/lint.yml").read_text(
        encoding="utf-8"
    )

    assert "Run offline tests with coverage" in workflow
    assert "--cov=mcp_server_phytomni" in workflow
    assert "--cov=mcp_client_phytomni" in workflow
    assert "--cov-report=term-missing" in workflow
    assert "--cov-report=xml" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "coverage.xml" in workflow


def test_ci_exercises_minimum_and_latest_dependency_resolution() -> None:
    """CI must test both direct dependency floors and latest resolution."""
    workflow = (_project_root() / ".github/workflows/lint.yml").read_text(
        encoding="utf-8"
    )
    floor_job = workflow.split("\n  dependency-floor:", 1)[1].split(
        "\n  black:", 1
    )[0]
    pylint_job = workflow.split("\n  pylint:", 1)[1].split("\n  pytest:", 1)[0]

    assert "dependency-floor:" in workflow
    assert "--resolution lowest-direct" in workflow
    assert "Run offline tests at dependency floors" in workflow
    assert "uv pip check --system" in floor_job
    assert "pytest" in floor_job
    assert "validate_local.sh" not in floor_job
    assert 'python-version: ["3.12", "3.13", "3.14"]' in workflow
    assert 'uv pip install --system -e ".[dev]"' in workflow
    assert "--disable=R0801,R0903" in pylint_job
    assert "scripts/check_pylint_baseline.py" in pylint_job


def test_scoped_gate_reconciles_static_analysis_exemptions() -> None:
    """Scoped Python and policy changes use the shared checker."""
    scoped_gate = (_project_root() / "scripts/scoped_gate.sh").read_text(
        encoding="utf-8"
    )

    assert "check --scope cross-file" in scoped_gate
    assert "check --scope full" in scoped_gate
    assert "static-analysis-exemptions.toml" in scoped_gate


def test_validate_local_uses_generated_static_analysis_contract() -> None:
    """The full gate delegates matching and ledger drift checks."""
    validate_local = (_project_root() / "scripts/validate_local.sh").read_text(
        encoding="utf-8"
    )

    assert "render-docs --check" in validate_local
    assert "check --scope full" in validate_local
