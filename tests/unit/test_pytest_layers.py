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

    assert EXPECTED_MARKER_PREFIXES <= marker_prefixes


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
    group_dev = pyproject["dependency-groups"]["dev"]
    coverage_config = pyproject["tool"]["coverage"]

    assert any(dep.startswith("pytest-cov") for dep in optional_dev)
    assert any(dep.startswith("pytest-cov") for dep in group_dev)
    assert coverage_config["run"] == {
        "branch": True,
        "relative_files": True,
        "source": ["mcp_server_phytomni"],
    }
    assert coverage_config["report"] == {
        "show_missing": True,
        "skip_covered": False,
    }
    assert coverage_config["xml"] == {"output": "coverage.xml"}


def test_ci_pytest_job_writes_coverage_report():
    """Verify ci pytest job writes coverage report."""
    workflow = (_project_root() / ".github/workflows/lint.yml").read_text(
        encoding="utf-8"
    )

    assert "Run offline tests with coverage" in workflow
    assert "--cov=mcp_server_phytomni" in workflow
    assert "--cov-report=term-missing" in workflow
    assert "--cov-report=xml" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "coverage.xml" in workflow
