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
    return Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads(
        (_project_root() / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_pytest_default_run_is_offline_and_strict():
    pytest_options = _pyproject()["tool"]["pytest"]["ini_options"]

    assert pytest_options["testpaths"] == ["tests"]
    assert pytest_options["pythonpath"] == ["src"]
    assert "--strict-config" in pytest_options["addopts"]
    assert "--strict-markers" in pytest_options["addopts"]
    assert "not integration and not network" in pytest_options["addopts"]


def test_pytest_layer_markers_are_registered():
    markers = set(_pyproject()["tool"]["pytest"]["ini_options"]["markers"])
    marker_prefixes = {marker.split(" ", maxsplit=1)[0] for marker in markers}

    assert EXPECTED_MARKER_PREFIXES <= marker_prefixes


def test_test_files_live_in_named_pytest_layers():
    tests_root = _project_root() / "tests"
    bad_paths = [
        path.relative_to(tests_root).as_posix()
        for path in tests_root.rglob("test_*.py")
        if path.relative_to(tests_root).parts[0] not in EXPECTED_LAYER_DIRS
    ]

    assert bad_paths == []
