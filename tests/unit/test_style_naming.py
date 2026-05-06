# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for repository naming and header conventions."""

import ast
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PYTHON_FILE_PATTERN = re.compile(r"^(_?[a-z][a-z0-9_]*|__init__)\.py$")
COPYRIGHT_HEADER = (
    "# Copyright (c) Biotechnology Research Institute,",
    "# Chinese Academy of Agricultural Sciences. 2024-2026. "
    "All rights reserved.",
)
AUTHOR_FIRST_LINE_PATTERN = re.compile(
    r"^# Author: [A-Za-z0-9_.-]+ \([^@\s)]+@[^@\s)]+\.[^@\s)]+\)$"
)
AUTHOR_CONTINUATION_PATTERN = re.compile(
    r"^#         [A-Za-z0-9_.-]+ \([^@\s)]+@[^@\s)]+\.[^@\s)]+\)$"
)


def test_python_file_names_follow_snake_case():
    root = Path(__file__).resolve().parents[2]
    bad_names = [
        path.relative_to(root).as_posix()
        for base in (root / "src", root / "tests")
        for path in base.rglob("*.py")
        if not PYTHON_FILE_PATTERN.fullmatch(path.name)
    ]

    assert bad_names == []


def test_python_files_use_standard_header_and_module_docstring():
    root = Path(__file__).resolve().parents[2]
    failures = []

    for base in (root / "src", root / "tests"):
        for path in base.rglob("*.py"):
            lines = path.read_text(encoding="utf-8").splitlines()
            relative_path = path.relative_to(root).as_posix()
            if tuple(lines[:2]) != COPYRIGHT_HEADER:
                failures.append(f"{relative_path}: copyright header")
                continue

            author_lines = []
            for line in lines[2:]:
                if line.startswith("# Author: ") or line.startswith(
                    "#         "
                ):
                    author_lines.append(line)
                    continue
                break
            if not author_lines:
                failures.append(f"{relative_path}: author header")
                continue
            if not AUTHOR_FIRST_LINE_PATTERN.fullmatch(author_lines[0]):
                failures.append(f"{relative_path}: first author line")
            for line in author_lines[1:]:
                if not AUTHOR_CONTINUATION_PATTERN.fullmatch(line):
                    failures.append(
                        f"{relative_path}: continuation author line"
                    )

            docstring_index = 2 + len(author_lines)
            if docstring_index >= len(lines) or not lines[
                docstring_index
            ].startswith('"""'):
                failures.append(f"{relative_path}: immediate module docstring")
                continue

            module = ast.parse(path.read_text(encoding="utf-8"))
            module_docstring = ast.get_docstring(module)
            if not module_docstring:
                failures.append(f"{relative_path}: missing module docstring")
            elif len(module_docstring.splitlines()) > 8:
                failures.append(f"{relative_path}: long module docstring")

    assert not failures


def test_ruff_enforces_import_grouping_and_sorting():
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    ruff_lint = pyproject["tool"]["ruff"]["lint"]
    assert "I" in ruff_lint["extend-select"]
    assert pyproject["tool"]["ruff"]["lint"]["isort"] == {
        "known-first-party": ["mcp_server_phytomni"]
    }
