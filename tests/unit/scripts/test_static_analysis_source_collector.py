# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for token-aware inline static-analysis suppression collection."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.static_analysis.collectors.source import (
    collect_source_suppressions,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("comment", "tool", "rule"),
    [
        (
            "# " + "pylint:" + " disable=protected-access",
            "pylint",
            "protected-access",
        ),
        (
            "# " + "pylint:" + " disable-next=too-many-locals",
            "pylint",
            "too-many-locals",
        ),
        ("# noqa: ASYNC110", "ruff", "ASYNC110"),
        ("# type: ignore[prop-decorator]", "mypy", "prop-decorator"),
        (
            "# pyright: ignore[reportArgumentType]",
            "pyright",
            "reportArgumentType",
        ),
        ("# nosec", "secret-scan", "*"),
        ("# pragma: allowlist secret", "secret-scan", "*"),
    ],
)
def test_collects_every_supported_inline_form(
    tmp_path: Path, comment: str, tool: str, rule: str
) -> None:
    """Each supported comment form becomes one exact Finding."""
    path = tmp_path / "sample.py"
    path.write_text(
        f"def sample():\n    value = 1  {comment}\n", encoding="utf-8"
    )

    finding = collect_source_suppressions(tmp_path, [path])[0]

    assert (finding.tool, finding.rule, finding.symbol) == (
        tool,
        rule,
        "sample",
    )
    assert finding.path == "sample.py"


def test_splits_rule_lists_and_binds_disable_next_to_next_symbol(
    tmp_path: Path,
) -> None:
    """Comma-separated rules produce separate findings at the applied line."""
    path = tmp_path / "sample.py"
    path.write_text(
        "# " + "pylint:" + " disable=protected-access, too-many-locals\n"
        "def sample():\n"
        "    # " + "pylint:" + " disable-next=unused-argument\n"
        "    return 1\n",
        encoding="utf-8",
    )

    findings = collect_source_suppressions(tmp_path, [path])

    assert [(item.tool, item.rule) for item in findings] == [
        ("pylint", "protected-access"),
        ("pylint", "too-many-locals"),
        ("pylint", "unused-argument"),
    ]
    assert findings[0].symbol is None
    assert findings[1].symbol is None
    assert findings[2].symbol == "sample"


def test_bare_directives_are_reported_for_reconciler_rejection(
    tmp_path: Path,
) -> None:
    """Bare directives become explicit wildcard findings."""
    path = tmp_path / "sample.py"
    path.write_text(
        "def sample():\n"
        "    value = 1  # noqa\n"
        "    other = 2  # type: ignore\n",
        encoding="utf-8",
    )

    findings = collect_source_suppressions(tmp_path, [path])

    assert [(item.tool, item.rule) for item in findings] == [
        ("ruff", "*"),
        ("mypy", "*"),
    ]


def test_prose_containing_noqa_is_ignored(tmp_path: Path) -> None:
    """Ordinary prose in comments is not treated as authorization."""
    path = tmp_path / "sample.py"
    path.write_text(
        "def sample():\n    text = 'noqa is a word'\n"
        "    # the documentation mentions noqa without a directive\n",
        encoding="utf-8",
    )

    assert not collect_source_suppressions(tmp_path, [path])


def test_module_level_directive_uses_span_target(tmp_path: Path) -> None:
    """A directive outside a definition remains an exact source span."""
    path = tmp_path / "sample.py"
    path.write_text("# noqa: E501\nvalue = 1\n", encoding="utf-8")

    finding = collect_source_suppressions(tmp_path, [path])[0]

    assert finding.symbol is None
    assert finding.target_kind.value == "span"


def test_repeated_directives_bind_to_following_definitions(
    tmp_path: Path,
) -> None:
    """Repeated pre-definition directives keep distinct exact targets."""
    path = tmp_path / "sample.py"
    path.write_text(
        "# pylint: disable=too-many-arguments\n"
        "@decorator\n"
        "def first(value):\n"
        "    return value\n\n"
        "# pylint: disable=too-many-arguments\n"
        "@decorator\n"
        "def second(value):\n"
        "    return value\n",
        encoding="utf-8",
    )

    findings = collect_source_suppressions(tmp_path, [path])

    assert [(item.rule, item.symbol) for item in findings] == [
        ("too-many-arguments", "first"),
        ("too-many-arguments", "second"),
    ]
    assert findings[0].fingerprint != findings[1].fingerprint


def test_indented_import_directive_uses_span_fallback(tmp_path: Path) -> None:
    """Indented import members use the safe span normalization fallback."""
    path = tmp_path / "bootstrap.py"
    path.write_text(
        "from package import (\n" + "    value,  # noqa: E402\n" + ")\n",
        encoding="utf-8",
    )

    finding = collect_source_suppressions(tmp_path, [path])[0]

    assert finding.tool == "ruff"
    assert finding.rule == "E402"
    assert finding.symbol is None
