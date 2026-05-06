# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for repository naming conventions."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PYTHON_FILE_PATTERN = re.compile(r"^(_?[a-z][a-z0-9_]*|__init__)\.py$")


def test_python_file_names_follow_snake_case():
    root = Path(__file__).resolve().parents[2]
    bad_names = [
        path.relative_to(root).as_posix()
        for base in (root / "src", root / "tests")
        for path in base.rglob("*.py")
        if not PYTHON_FILE_PATTERN.fullmatch(path.name)
    ]

    assert bad_names == []
