# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for repository naming and header conventions."""

import ast
import configparser
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
PYLINT_DISABLE_MARKER = "".join(("pylint:", " disable="))
ALLOWED_LOCAL_PYLINT_DISABLES = {
    "src/mcp_server_phytomni/langgraph_runner.py": {
        "broad-exception-caught",
    },
}


def test_python_file_names_follow_snake_case():
    """Verify python file names follow snake case."""
    root = Path(__file__).resolve().parents[2]
    bad_names = [
        path.relative_to(root).as_posix()
        for base in (root / "src", root / "tests")
        for path in base.rglob("*.py")
        if not PYTHON_FILE_PATTERN.fullmatch(path.name)
    ]

    assert bad_names == []


def test_python_files_use_standard_header_and_module_docstring():
    """Verify python files use standard header and module docstring."""
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
    """Verify ruff enforces import grouping and sorting."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    ruff_lint = pyproject["tool"]["ruff"]["lint"]
    assert "I" in ruff_lint["extend-select"]
    assert pyproject["tool"]["ruff"]["lint"]["isort"] == {
        "known-first-party": ["mcp_server_phytomni"]
    }


def test_flake8_uses_black_compatible_style_without_init_ignores():
    """Verify flake8 uses black compatible style without init ignores."""
    root = Path(__file__).resolve().parents[2]
    parser = configparser.ConfigParser()
    parser.read(root / ".flake8", encoding="utf-8")

    flake8_config = parser["flake8"]
    ignored_rules = {
        rule.strip()
        for rule in flake8_config["extend-ignore"].split(",")
        if rule.strip()
    }

    assert ignored_rules == {"E203", "W503"}
    assert "per-file-ignores" not in flake8_config


def test_function_docstring_waiver_is_removed_from_tests():
    """Verify function docstring waivers do not return to tests."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    pylint_disable = set(
        pyproject["tool"]["pylint"]
        .get("messages_control", {})
        .get("disable", [])
    )
    assert "missing-function-docstring" not in pylint_disable

    violations = []
    for path in (root / "tests").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if (
                PYLINT_DISABLE_MARKER in line
                and "missing-function-docstring" in line
            ):
                violations.append(path.relative_to(root).as_posix())

    assert not violations


def test_global_pylint_disables_are_not_reintroduced():
    """Verify global pylint disables are not reintroduced."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    pylint_disable = (
        pyproject["tool"]["pylint"]
        .get("messages_control", {})
        .get("disable", [])
    )

    assert pylint_disable == []


def test_local_pylint_disables_are_langgraph_boundary_only():
    """Verify local pylint disables stay limited to LangGraph boundaries."""
    root = Path(__file__).resolve().parents[2]
    violations = []

    for base in (root / "src", root / "tests"):
        for path in base.rglob("*.py"):
            relative_path = path.relative_to(root).as_posix()
            allowed_rules = ALLOWED_LOCAL_PYLINT_DISABLES.get(
                relative_path, set()
            )
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if PYLINT_DISABLE_MARKER not in line:
                    continue
                disabled_rules = {
                    rule.strip()
                    for rule in line.split(PYLINT_DISABLE_MARKER, 1)[1].split(
                        ","
                    )
                    if rule.strip()
                }
                if disabled_rules - allowed_rules:
                    violations.append(f"{relative_path}:{line_number}")

    assert not violations
