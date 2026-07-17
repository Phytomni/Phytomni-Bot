# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for configuration, warning, CI, and secret collectors."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.static_analysis.collectors.ci import collect_ci_suppressions
from scripts.static_analysis.collectors.config import (
    collect_config_suppressions,
)
from scripts.static_analysis.collectors.secrets import collect_secret_markers
from scripts.static_analysis.collectors.warnings import (
    collect_warning_suppressions,
)

pytestmark = pytest.mark.unit


def test_config_collector_splits_global_rule_lists(tmp_path: Path) -> None:
    """TOML rule lists become one exact finding per configured rule."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\n"
        'ignore = ["ASYNC109"]\n'
        "[tool.pymarkdown.plugins.md013]\n"
        "enabled = false\n",
        encoding="utf-8",
    )

    findings = collect_config_suppressions(tmp_path)

    assert {(item.tool, item.rule) for item in findings} == {
        ("ruff", "ASYNC109"),
        ("pymarkdown", "md013"),
    }
    assert all(item.target_kind.value == "config" for item in findings)


def test_config_collector_covers_pylint_mypy_and_flake8(
    tmp_path: Path,
) -> None:
    """Important project-level ignores retain exact section/key identities."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pylint.format]\n"
        "max-module-lines = 1100\n"
        '[tool.pylint."messages control"]\n'
        'disable = ["R0801"]\n'
        "[[tool.mypy.overrides]]\n"
        'module = ["third_party"]\n'
        "ignore_missing_imports = true\n",
        encoding="utf-8",
    )
    (tmp_path / ".flake8").write_text(
        "[flake8]\nextend-ignore = E203,W503\n", encoding="utf-8"
    )

    findings = collect_config_suppressions(tmp_path)
    pairs = {(item.tool, item.rule) for item in findings}

    assert ("pylint", "max-module-lines") in pairs
    assert ("pylint", "R0801") in pairs
    assert ("mypy", "ignore_missing_imports") in pairs
    assert ("flake8", "E203") in pairs
    assert ("flake8", "W503") in pairs


def test_warning_collector_reads_config_and_decorators(tmp_path: Path) -> None:
    """Warning filters retain their expression and owning test symbol."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'filterwarnings = ["ignore::UserWarning"]\n',
        encoding="utf-8",
    )
    path = tmp_path / "test_sample.py"
    path.write_text(
        "import pytest\n\n"
        '@pytest.mark.filterwarnings("ignore::DeprecationWarning")\n'
        "def test_sample():\n    pass\n",
        encoding="utf-8",
    )

    findings = collect_warning_suppressions(tmp_path, [path])

    assert [(item.tool, item.rule) for item in findings] == [
        ("pytest", "ignore::UserWarning"),
        ("pytest", "ignore::DeprecationWarning"),
    ]
    assert findings[1].symbol == "test_sample"


def test_ci_collector_finds_command_flags_and_old_baseline(
    tmp_path: Path,
) -> None:
    """CI and local gate suppressions remain independently visible."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "lint.yml").write_text(
        "run: pylint --disable=R0801,R0903 src\n", encoding="utf-8"
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "validate_local.sh").write_text(
        "uv run python scripts/check_pylint_baseline.py\n", encoding="utf-8"
    )

    findings = collect_ci_suppressions(tmp_path)

    assert [(item.tool, item.rule) for item in findings] == [
        ("pylint", "R0801"),
        ("pylint", "R0903"),
        ("pylint", "numeric-baseline"),
    ]
    assert all(item.target_kind.value == "command" for item in findings)


def test_secret_collector_handles_python_and_markdown(tmp_path: Path) -> None:
    """Secret allowlist markers are collected across source file kinds."""
    python_path = tmp_path / "sample.py"
    python_path.write_text("value = 'token'  # nosec B105\n", encoding="utf-8")
    markdown_path = tmp_path / "README.md"
    markdown_path.write_text(
        "example: token  # pragma: allowlist secret\n", encoding="utf-8"
    )

    findings = collect_secret_markers(tmp_path, [python_path, markdown_path])

    assert [(item.path, item.rule) for item in findings] == [
        ("README.md", "*"),
        ("sample.py", "B105"),
    ]


def test_secret_collector_skips_binary_tracked_artifacts(
    tmp_path: Path,
) -> None:
    """Binary fixtures are not comment-bearing source and cannot be decoded."""
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"\x93\x00\xff")

    assert not collect_secret_markers(tmp_path, [path])
