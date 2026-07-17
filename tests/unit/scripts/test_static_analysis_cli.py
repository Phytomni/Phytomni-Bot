# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the static-analysis exemption CLI."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
import scripts.check_static_analysis_exemptions as cli
from scripts.static_analysis.collectors.errors import CollectionError
from scripts.static_analysis.collectors.pylint import (
    validate_full_pylint_result,
)
from scripts.static_analysis.model import (
    Classification,
    Exemption,
    Finding,
    Mechanism,
    Registry,
    TargetKind,
)

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


def test_cross_file_scope_excludes_command_baseline_records() -> None:
    """Partial cross-file checks do not stale command-level suppressions."""
    registry = cli.load_registry(
        Path(__file__).resolve().parents[3]
        / "static-analysis-exemptions.toml",
        today=date(2026, 7, 17),
    )
    scoped = getattr(cli, "_registry_for_scope")(registry, "cross-file")

    assert scoped.exemptions
    assert {
        (item.tool, item.rule, item.mechanism.value)
        for item in scoped.exemptions
    } == {
        ("pylint", "R0801", "diagnostic"),
        ("pylint", "R0903", "diagnostic"),
    }


def test_check_pylint_reads_nul_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stdin path protocol preserves spaces and trailing NULs."""
    paths = ("tests/fixture with spaces.py", "scripts/example.py")
    monkeypatch.setattr(cli, "tracked_python_files", lambda _root: paths)
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(("\0".join(paths) + "\0").encode())),
    )
    observed: dict[str, tuple[str, ...]] = {}

    def run_pylint(
        _root: Path,
        files: tuple[str, ...],
        _python_version: str,
    ) -> tuple[str, tuple[dict[str, object], ...]]:
        observed["files"] = files
        return "pylint 4.0.6", ()

    monkeypatch.setattr(cli, "run_full_pylint", run_pylint)

    assert (
        cli.main(
            [
                "check-pylint",
                "--python-version",
                "3.12",
                "--files-from-stdin",
            ]
        )
        == 0
    )
    assert observed["files"] == paths


def test_check_pylint_rejects_empty_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty changed-file stream fails closed before Pylint runs."""
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))

    assert (
        cli.main(
            [
                "check-pylint",
                "--python-version",
                "3.12",
                "--files-from-stdin",
            ]
        )
        == 2
    )


def test_check_pylint_rejects_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed report is an invocation error, never a clean run."""
    monkeypatch.setattr(
        cli,
        "tracked_python_files",
        lambda _root: ("scripts/example.py",),
    )

    def malformed(*_args: object, **_kwargs: object) -> tuple:
        raise CollectionError("pylint returned malformed JSON")

    monkeypatch.setattr(cli, "run_full_pylint", malformed)

    assert (
        cli.main(
            [
                "check-pylint",
                "--python-version",
                "3.12",
                "--files-from-git",
            ]
        )
        == 2
    )


def test_check_pylint_fails_on_ordinary_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All non-cross-file diagnostics remain ordinary gate failures."""
    monkeypatch.setattr(
        cli,
        "tracked_python_files",
        lambda _root: ("scripts/example.py",),
    )
    monkeypatch.setattr(
        cli,
        "run_full_pylint",
        lambda *_args, **_kwargs: (
            "pylint 4.0.6",
            (
                {
                    "message-id": "E0001",
                    "path": "scripts/example.py",
                    "line": 1,
                    "column": 0,
                    "message": "ordinary failure",
                },
            ),
        ),
    )

    assert (
        cli.main(
            [
                "check-pylint",
                "--python-version",
                "3.12",
                "--files-from-git",
            ]
        )
        == 1
    )


def test_check_pylint_accepts_registered_refactor_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact registry identity, not its rule name alone, may pass."""
    finding = Finding(
        tool="pylint",
        rule="R0903",
        mechanism=Mechanism.DIAGNOSTIC,
        target_kind=TargetKind.SYMBOL,
        path="scripts/example.py",
        symbol="Fake",
        peer_path=None,
        peer_symbol=None,
        fingerprint="sha256:" + "2" * 64,
        location="scripts/example.py:1",
        message="Too few public methods (1/2)",
        tool_version="pylint 4.0.6",
    )
    exemption = Exemption(
        id="SAE-TEST-0001",
        tool=finding.tool,
        rule=finding.rule,
        classification=Classification.STRUCTURAL,
        mechanism=finding.mechanism,
        target_kind=finding.target_kind,
        path=finding.path,
        symbol=finding.symbol,
        peer_path=finding.peer_path,
        peer_symbol=finding.peer_symbol,
        fingerprint=finding.fingerprint,
        owner="test",
        introduced_on=date(2026, 7, 17),
        review_on=date(2026, 8, 15),
        rationale="A tested fake boundary.",
        counterfactual="Refactor only with a contract change.",
        risk="A future fake could hide a regression.",
        tests=("test_check_pylint",),
        expires_on=None,
        remediation=None,
    )
    monkeypatch.setattr(
        cli,
        "tracked_python_files",
        lambda _root: ("scripts/example.py",),
    )
    monkeypatch.setattr(
        cli,
        "run_full_pylint",
        lambda *_args, **_kwargs: (
            "pylint 4.0.6",
            ({"message-id": "R0903"},),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_cross_records",
        lambda *_args, **_kwargs: (finding,),
    )
    monkeypatch.setattr(
        cli,
        "tracked_python_files_with_stubs",
        lambda _root: ("scripts/example.py",),
    )
    monkeypatch.setattr(
        cli,
        "run_cross_file_pylint",
        lambda *_args, **_kwargs: (finding,),
    )
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda *_args, **_kwargs: Registry(
            schema_version=1,
            default="deny",
            exemptions=(exemption,),
        ),
    )

    assert (
        cli.main(
            [
                "check-pylint",
                "--python-version",
                "3.12",
                "--files-from-git",
            ]
        )
        == 0
    )


def test_check_pylint_rejects_unexpected_status_bits() -> None:
    """Only documented diagnostic bits may accompany a JSON report."""
    assert validate_full_pylint_result(24, "[]", "") == "[]"
    for status in (1, 32, 64):
        with pytest.raises(CollectionError, match="unexpected status"):
            validate_full_pylint_result(status, "[]", "")
