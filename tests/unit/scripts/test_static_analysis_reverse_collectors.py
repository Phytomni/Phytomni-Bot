# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for fail-closed reverse suppression probes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.static_analysis.collectors import CollectionError
from scripts.static_analysis.collectors.flake8 import audit_flake8
from scripts.static_analysis.collectors.ruff import audit_ruff
from scripts.static_analysis.collectors.typing import audit_mypy, audit_pyright
from scripts.static_analysis.model import Finding, Mechanism, TargetKind

pytestmark = pytest.mark.unit


def _finding(tool: str, rule: str) -> Finding:
    return Finding(
        tool=tool,
        rule=rule,
        mechanism=Mechanism.INLINE,
        target_kind=TargetKind.SYMBOL,
        path="sample.py",
        symbol="sample",
        peer_path=None,
        peer_symbol=None,
        fingerprint="sha256:" + "a" * 64,
        location="sample.py:1",
        message="suppression",
        tool_version=None,
    )


def _runner_for(version: str, stdout: str, returncode: int = 1):
    def runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, version, "")
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    return runner


def test_ruff_reverse_probe_requires_matching_json_diagnostic(
    tmp_path: Path,
) -> None:
    """Ruff evidence records version, command, and the matched code."""
    evidence = audit_ruff(
        tmp_path,
        _finding("ruff", "ASYNC110"),
        _runner_for("ruff 0.15.12", '[{"code":"ASYNC110"}]'),
    )

    assert evidence.necessary
    assert evidence.returncode == 1
    assert evidence.output.startswith("ruff 0.15.12")
    assert "--ignore-noqa" in evidence.command


def test_flake8_and_mypy_reverse_probes_match_text_codes(
    tmp_path: Path,
) -> None:
    """Text-output tools retain the exact code in their reverse evidence."""
    runner = _runner_for("flake8 7.3.0", "sample.py:1:1: E203 spacing")
    flake8 = audit_flake8(tmp_path, _finding("flake8", "E203"), runner)
    assert flake8.necessary

    mypy = audit_mypy(
        tmp_path,
        _finding("mypy", "prop-decorator"),
        _runner_for("mypy 2.0.0", "sample.py:1: error: prop-decorator"),
    )
    assert mypy.necessary


def test_pyright_reverse_probe_matches_general_diagnostics(
    tmp_path: Path,
) -> None:
    """Pyright JSON diagnostics use its camelCase rule identifier."""
    evidence = audit_pyright(
        tmp_path,
        _finding("pyright", "reportArgumentType"),
        _runner_for(
            "pyright 1.1.409",
            '{"generalDiagnostics":[{"rule":"reportArgumentType"}]}',
        ),
    )
    assert evidence.necessary


@pytest.mark.parametrize(
    "stdout,returncode,error",
    [
        ("", 1, "empty output"),
        ("not-json", 1, "malformed JSON"),
        ('[{"code":"E501"}]', 1, "stale suppression"),
    ],
)
def test_reverse_probe_fails_closed(
    tmp_path: Path, stdout: str, returncode: int, error: str
) -> None:
    """Empty, malformed, and non-matching tool output cannot pass."""
    with pytest.raises(CollectionError, match=error):
        audit_ruff(
            tmp_path,
            _finding("ruff", "ASYNC110"),
            _runner_for("ruff 0.15.12", stdout, returncode),
        )


def test_reverse_probe_rejects_unexpected_status(tmp_path: Path) -> None:
    """Fatal tool statuses are invocation errors, not clean findings."""
    with pytest.raises(CollectionError, match="unexpected status"):
        audit_flake8(
            tmp_path,
            _finding("flake8", "E203"),
            _runner_for("flake8 7.3.0", "E203", returncode=2),
        )
