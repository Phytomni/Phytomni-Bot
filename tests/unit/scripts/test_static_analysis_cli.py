# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the static-analysis exemption CLI."""

from __future__ import annotations

import pytest
import scripts.check_static_analysis_exemptions as cli
from scripts.static_analysis.collectors.errors import CollectionError
from scripts.static_analysis.model import Finding, Mechanism, TargetKind

pytestmark = pytest.mark.unit


def make_finding() -> Finding:
    """Build the minimal complete finding used by candidate output tests."""
    values = (
        "ruff",
        "E501",
        Mechanism.INLINE,
        TargetKind.SYMBOL,
        "src/example.py",
        "sample",
        None,
        None,
        "sha256:" + "1" * 64,
        "src/example.py:1",
        "line too long",
        "ruff 0.12.0",
    )
    return Finding(*values)


def test_cli_does_not_treat_empty_failed_tool_as_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collector invocation failure is exit 2, not a clean report."""

    def failed_inventory(*_args: object, **_kwargs: object) -> tuple:
        raise CollectionError("fatal")

    monkeypatch.setattr(cli, "collect_inventory", failed_inventory)
    assert cli.main(["check", "--scope", "cross-file"]) == 2


def test_candidate_flags_are_toml_only() -> None:
    """Review/JSON output cannot be mixed with candidate-only flags."""
    assert (
        cli.main(
            [
                "inventory",
                "--format",
                "review",
                "--candidate-classification",
                "temporary",
            ]
        )
        == 2
    )


def test_cli_rejects_structural_expiry() -> None:
    """Structural candidates cannot carry a temporary lifecycle."""
    assert (
        cli.main(
            [
                "inventory",
                "--format",
                "toml",
                "--candidate-classification",
                "structural",
                "--candidate-expires-on",
                "2026-08-31",
            ]
        )
        == 2
    )


def test_cli_inventory_candidate_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TOML candidate output includes stable identifiers and exact fields."""
    monkeypatch.setattr(
        cli,
        "collect_inventory",
        lambda *_args, **_kwargs: (make_finding(),),
    )
    assert (
        cli.main(
            [
                "inventory",
                "--format",
                "toml",
                "--candidate-classification",
                "temporary",
                "--candidate-expires-on",
                "2026-08-31",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert 'id = "SAE-TMP-0001"' in output
    assert 'rule = "E501"' in output


def test_cli_render_docs_check_reports_drift(tmp_path) -> None:
    """The render check returns one when the generated file differs."""
    registry = tmp_path / "registry.toml"
    registry.write_text(
        'schema_version = 1\n\n[policy]\ndefault = "deny"\n',
        encoding="utf-8",
    )
    output = tmp_path / "ledger.md"
    output.write_text("stale\n", encoding="utf-8")
    assert (
        cli.main(
            [
                "render-docs",
                "--registry",
                str(registry),
                "--output",
                str(output),
                "--check",
            ]
        )
        == 1
    )
