# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for supported Python dependency resolution."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _runtime_dependencies() -> list[str]:
    """Return the declared runtime dependency strings."""
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["project"]["dependencies"]


def test_python_314_runtime_dependency_floors_are_explicit() -> None:
    """Keep supported Python 3.14 dependency floors explicit."""
    dependencies = _runtime_dependencies()

    assert "onnxruntime>=1.25.1" in dependencies
    assert "youtube-transcript-api>=1.2.4" in dependencies
    markitdown_dependency = (
        "markitdown[az-doc-intel,audio-transcription,docx,outlook,pdf,"
        "pptx,xls,xlsx]>=0.1.5"
    )
    assert markitdown_dependency in dependencies
    assert not any(
        dependency.startswith("markitdown[all]") for dependency in dependencies
    )
