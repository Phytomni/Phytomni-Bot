# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests that keep release version surfaces synchronized."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from mcp_server_phytomni import __version__
from mcp_server_phytomni.api.app import create_app

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_surfaces_agree() -> None:
    """Wheel metadata, package runtime, and FastAPI report one version."""
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project_version = pyproject["project"]["version"]

    assert project_version == "0.1.3"
    assert __version__ == project_version
    assert version("mcp-server-phytomni") == project_version
    assert create_app().version == project_version
