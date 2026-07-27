# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Safety tests for current-SHA contract evidence capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import capture_contract_evidence as capture

pytestmark = pytest.mark.unit

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _log(*, sha: str = _SHA, exit_code: int = 0) -> str:
    """Build one sanitized gate log with an explicit exit marker."""
    return f"bot_sha={sha}\nexit_code={exit_code}\npassed\n"


def _matrix() -> dict[str, str]:
    """Return the complete supported-version result."""
    return {"3.12": "success", "3.13": "success", "3.14": "success"}


def test_parse_gate_log_requires_current_sha_and_zero_exit() -> None:
    """A gate log must identify the exact SHA and pass explicitly."""
    assert capture.parse_gate_log(_log()) == 0
    with pytest.raises(capture.CaptureError, match="SHA"):
        capture.parse_gate_log(_log(sha="f" * 40), expected_sha=_SHA)
    with pytest.raises(capture.CaptureError, match="exit code"):
        capture.parse_gate_log(_log(exit_code=1), expected_sha=_SHA)


def test_parse_gate_log_rejects_short_and_multiple_sha_values() -> None:
    """Short or ambiguous SHA evidence cannot become a packet."""
    with pytest.raises(capture.CaptureError, match="full 40-character"):
        capture.parse_gate_log("bot_sha=short\nexit_code=0\n")
    with pytest.raises(capture.CaptureError, match="exactly one"):
        capture.parse_gate_log(
            f"bot_sha={_SHA}\nbot_sha={'f' * 40}\nexit_code=0\n"
        )


def test_parse_python_matrix_requires_all_supported_versions() -> None:
    """Every supported interpreter must have a successful current result."""
    assert capture.parse_python_matrix(_matrix()) == _matrix()
    with pytest.raises(capture.CaptureError, match="3.14"):
        capture.parse_python_matrix({"3.12": "success", "3.13": "success"})
    with pytest.raises(capture.CaptureError, match="success"):
        capture.parse_python_matrix(
            {"3.12": "success", "3.13": "failure", "3.14": "success"}
        )


def test_public_json_rejects_sensitive_evidence() -> None:
    """Evidence bodies reject credentials, paths, SQL, and provider text."""
    capture.validate_public_json({"answer": "synthetic"})
    for value in (
        "Bearer top-secret",
        "/home/operator/private.json",
        "SELECT * FROM runs",
        "PROVIDER-PAYLOAD",
    ):
        with pytest.raises(capture.CaptureError, match="forbidden"):
            capture.validate_public_json({"value": value})


def test_output_root_must_be_outside_repository(tmp_path: Path) -> None:
    """The self-referential packet cannot be written into the checkout."""
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(capture.CaptureError, match="outside"):
        capture.ensure_output_root(repository, repository / "evidence")
    capture.ensure_output_root(repository, tmp_path / "evidence")


def test_write_artifact_is_current_and_redacted(tmp_path: Path) -> None:
    """The packet writer emits manifest, hashes, logs, and matrix evidence."""
    repository = tmp_path / "repo"
    repository.mkdir()
    output_root = tmp_path / "evidence"
    evidence = capture.AcceptanceEvidence(
        bot_sha=_SHA,
        branch="release/0.1.4",
        worktree_clean=True,
        hashes=capture.ContractHashes(
            fixture_sha256={"fixture.json": "a" * 64},
            http_body_sha256={"body.json": "b" * 64},
        ),
        gates=capture.GateEvidence(
            focused_exit_code=0,
            full_gate_exit_code=0,
            python_matrix=_matrix(),
        ),
        generated_at="2026-07-28T00:00:00+00:00",
    )
    packet = capture.write_artifact(
        repository,
        output_root,
        evidence,
        capture.ArtifactInputs(
            focused_log=_log(),
            full_gate_log=_log(),
            python_matrix=_matrix(),
            worktree_text="## release/0.1.4\n",
        ),
    )
    assert packet == output_root / _SHA
    manifest = json.loads(
        (packet / "acceptance.json").read_text(encoding="utf-8")
    )
    assert manifest["bot_sha"] == _SHA
    assert (packet / "fixture-sha256.txt").is_file()
    assert (packet / "http-body-sha256.txt").is_file()
    assert (packet / "python-matrix.json").is_file()
