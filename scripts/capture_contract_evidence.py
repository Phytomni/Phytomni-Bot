# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Capture a sanitized, current-SHA Bot contract evidence packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SHA_PATTERN = re.compile(r"\bbot_sha=([^\s]+)")
_EXIT_PATTERN = re.compile(r"\bexit_code=(-?\d+)\b")
_VERSION_PATTERN = re.compile(r"3\.1[234]")
_SHA_VALUE = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_TEXT = (
    "Bearer ",
    "/home/",
    "SELECT ",
    "PROVIDER-PAYLOAD",
    "PRIVATE_RESULT",
)
SUPPORTED_PYTHON = ("3.12", "3.13", "3.14")
A2UI_FIXTURES = (
    "docs/contracts/a2ui/chat_confirm/downlink.json",
    "docs/contracts/a2ui/chat_form/downlink.json",
    "docs/contracts/a2ui/chat_choice/downlink.json",
    "docs/contracts/a2ui/chat_confirm/success_accept.json",
    "docs/contracts/a2ui/chat_form/success_submit.json",
    "docs/contracts/a2ui/chat_form/success_cancel.json",
    "docs/contracts/a2ui/chat_choice/success_submit.json",
    "docs/contracts/a2ui/chat_choice/success_cancel.json",
    "docs/contracts/a2ui/multi_turn/round2_downlink.json",
)
HTTP_GOLDENS = (
    "docs/contracts/http/chat_terminal_succeeded.json",
    "docs/contracts/http/analyst_terminal_succeeded.json",
    "docs/contracts/http/review_terminal_succeeded.json",
    "docs/contracts/http/review_round2_input_required.json",
    "docs/contracts/http/error_400_run_widget_payload_mismatch.json",
    "docs/contracts/http/error_403_action_flag_off.json",
    "docs/contracts/http/error_404_owner_safe_not_found.json",
    "docs/contracts/http/error_409_already_handled.json",
    "docs/contracts/http/error_422_capability_validation.json",
    "docs/contracts/http/a2ui_request_64k_exact.json",
    "docs/contracts/http/a2ui_request_64k_plus_one.json",
    "docs/contracts/http/a2ui_response_1m_exact.json",
    "docs/contracts/http/a2ui_response_1m_plus_one.json",
    "docs/contracts/http/streamed_run_accumulated_answer.json",
    "docs/contracts/http/deep_genome_bounded_reports.json",
    "docs/contracts/http/dataagent_incident_replay_guarded.json",
    "docs/contracts/http/remote_partial_acceptance.json",
    "docs/contracts/http/remote_registry_degraded.json",
)

__all__ = [
    "A2UI_FIXTURES",
    "AcceptanceEvidence",
    "ArtifactInputs",
    "CaptureError",
    "CaptureOptions",
    "ContractHashes",
    "GateEvidence",
    "HTTP_GOLDENS",
    "SUPPORTED_PYTHON",
    "capture_evidence",
    "ensure_output_root",
    "main",
    "parse_gate_log",
    "parse_python_matrix",
    "validate_public_json",
    "write_artifact",
]


class CaptureError(RuntimeError):
    """Raised when evidence is incomplete, stale, or unsafe to publish."""


@dataclass(frozen=True, slots=True)
class ContractHashes:
    """Hashes for the two tracked public contract asset sets."""

    fixture_sha256: dict[str, str]
    http_body_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """Exit results for focused, full, and supported-version gates."""

    focused_exit_code: int
    full_gate_exit_code: int
    python_matrix: dict[str, str]


@dataclass(frozen=True, slots=True)
class AcceptanceEvidence:
    """Public manifest fields for one current-SHA evidence packet."""

    bot_sha: str
    branch: str
    worktree_clean: bool
    hashes: ContractHashes
    gates: GateEvidence
    generated_at: str


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """Input files and output boundary for one capture invocation."""

    focused_log: Path
    full_gate_log: Path
    python_matrix: Path
    output_root: Path
    allow_explained_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactInputs:
    """Sanitized text copied alongside the JSON manifest."""

    focused_log: str
    full_gate_log: str
    python_matrix: dict[str, str]
    worktree_text: str


def _validate_sha(value: str, *, label: str = "SHA") -> str:
    """Return a lower-case forty-character Git SHA or reject it."""
    if not _SHA_VALUE.fullmatch(value):
        raise CaptureError(f"{label} must be a full 40-character SHA")
    return value


def _validate_text(value: str, *, label: str) -> None:
    """Reject sensitive or private text before it enters the packet."""
    for forbidden in _FORBIDDEN_TEXT:
        if forbidden in value:
            raise CaptureError(f"{label} contains forbidden evidence text")


def validate_public_json(value: Any) -> None:
    """Validate a JSON body against the public evidence redaction boundary."""
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise CaptureError("evidence body is not JSON serializable") from exc
    _validate_text(serialized, label="JSON evidence")


def parse_gate_log(text: str, *, expected_sha: str | None = None) -> int:
    """Validate one log's exact SHA and explicit zero exit marker."""
    _validate_text(text, label="gate log")
    values = _SHA_PATTERN.findall(text)
    if len(values) != 1:
        raise CaptureError("gate log must contain exactly one bot_sha")
    observed_sha = _validate_sha(values[0], label="logged SHA")
    if expected_sha is not None and observed_sha != expected_sha:
        raise CaptureError("gate log SHA does not match current SHA")
    exits = _EXIT_PATTERN.findall(text)
    if len(exits) != 1:
        raise CaptureError("gate log must contain exactly one exit_code")
    exit_code = int(exits[0])
    if exit_code != 0:
        raise CaptureError(f"gate exit code is {exit_code}")
    return exit_code


def _matrix_from_jobs(value: Mapping[str, Any]) -> dict[str, str]:
    """Extract supported interpreter conclusions from a GitHub job payload."""
    jobs = value.get("jobs")
    if not isinstance(jobs, list):
        return {}
    result: dict[str, str] = {}
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        name = str(job.get("name", ""))
        match = _VERSION_PATTERN.search(name)
        conclusion = job.get("conclusion")
        if match and isinstance(conclusion, str):
            result[match.group()] = conclusion.lower()
    return result


def parse_python_matrix(value: Mapping[str, Any]) -> dict[str, str]:
    """Require successful Python 3.12, 3.13, and 3.14 evidence."""
    direct = {
        version: str(value[version]).lower()
        for version in SUPPORTED_PYTHON
        if version in value
    }
    statuses = direct or _matrix_from_jobs(value)
    missing = [
        version for version in SUPPORTED_PYTHON if version not in statuses
    ]
    if missing:
        raise CaptureError("Python matrix missing " + ", ".join(missing))
    failures = [
        version
        for version in SUPPORTED_PYTHON
        if statuses[version] != "success"
    ]
    if failures:
        raise CaptureError(
            "Python matrix requires success for " + ", ".join(failures)
        )
    return {version: statuses[version] for version in SUPPORTED_PYTHON}


def ensure_output_root(repository: Path, output_root: Path) -> Path:
    """Require an evidence destination outside the repository."""
    repository_path = repository.resolve()
    output_path = output_root.expanduser().resolve()
    if (
        output_path == repository_path
        or repository_path in output_path.parents
    ):
        raise CaptureError("evidence output must be outside the repository")
    return output_path


def _run_git(repository: Path, *args: str) -> str:
    """Run one read-only Git query and return stdout."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CaptureError("Git evidence query failed") from exc
    return completed.stdout.strip()


def _read_text(path: Path, *, label: str) -> str:
    """Read a UTF-8 evidence input without accepting missing files."""
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaptureError(f"{label} could not be read") from exc
    _validate_text(value, label=label)
    return value


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    """Read one JSON evidence input as an object."""
    try:
        value = json.loads(_read_text(path, label=label))
    except json.JSONDecodeError as exc:
        raise CaptureError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise CaptureError(f"{label} must be a JSON object")
    validate_public_json(value)
    return value


def _hash_contracts(repository: Path, paths: Sequence[str]) -> dict[str, str]:
    """Validate and hash the fixed public fixture list."""
    hashes: dict[str, str] = {}
    for relative in paths:
        path = repository / relative
        _read_json(path, label=relative)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CaptureError(f"{relative} could not be hashed") from exc
        hashes[relative] = hashlib.sha256(data).hexdigest()
    return hashes


def _dirty_paths(status: str) -> tuple[str, ...]:
    """Extract changed repository paths from porcelain status output."""
    paths: list[str] = []
    for line in status.splitlines():
        if not line or line.startswith("## "):
            continue
        raw = line[3:].strip() if len(line) >= 3 else ""
        if " -> " in raw:
            raw = raw.rsplit(" -> ", maxsplit=1)[-1]
        paths.append(raw)
    return tuple(paths)


def _validate_worktree(status: str, explained: Sequence[str]) -> bool:
    """Require a clean worktree except for explicitly named paths."""
    dirty = _dirty_paths(status)
    unexplained = sorted(set(dirty) - set(explained))
    if unexplained:
        raise CaptureError(
            "worktree has unexplained paths: " + ", ".join(unexplained)
        )
    return not dirty


def _write_text(path: Path, value: str) -> None:
    """Write one UTF-8 packet member with a final newline where needed."""
    try:
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        raise CaptureError("evidence packet could not be written") from exc


def _write_hashes(path: Path, values: Mapping[str, str]) -> None:
    """Write deterministic ``sha256  relative-path`` lines."""
    content = "".join(
        f"{digest}  {relative}\n"
        for relative, digest in sorted(values.items())
    )
    _write_text(path, content)


def write_artifact(
    repository: Path,
    output_root: Path,
    evidence: AcceptanceEvidence,
    inputs: ArtifactInputs,
) -> Path:
    """Write an immutable-shaped packet under ``output_root/<bot_sha>``."""
    destination_root = ensure_output_root(repository, output_root)
    _validate_sha(evidence.bot_sha)
    _validate_text(inputs.focused_log, label="focused log")
    _validate_text(inputs.full_gate_log, label="full gate log")
    _validate_text(inputs.worktree_text, label="worktree evidence")
    matrix = parse_python_matrix(inputs.python_matrix)
    destination = destination_root / evidence.bot_sha
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CaptureError(
            "evidence packet directory could not be created"
        ) from exc
    manifest = {
        "bot_sha": evidence.bot_sha,
        "branch": evidence.branch,
        "worktree_clean": evidence.worktree_clean,
        "fixture_sha256": evidence.hashes.fixture_sha256,
        "http_body_sha256": evidence.hashes.http_body_sha256,
        "focused_exit_code": evidence.gates.focused_exit_code,
        "full_gate_exit_code": evidence.gates.full_gate_exit_code,
        "python_matrix": matrix,
        "generated_at": evidence.generated_at,
    }
    try:
        rendered = json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CaptureError(
            "evidence manifest is not JSON serializable"
        ) from exc
    _write_text(destination / "acceptance.json", rendered + "\n")
    _write_hashes(
        destination / "fixture-sha256.txt",
        evidence.hashes.fixture_sha256,
    )
    _write_hashes(
        destination / "http-body-sha256.txt",
        evidence.hashes.http_body_sha256,
    )
    _write_text(destination / "focused-tests.log", inputs.focused_log)
    _write_text(destination / "full-gate.log", inputs.full_gate_log)
    _write_text(
        destination / "python-matrix.json",
        json.dumps(matrix, indent=2, sort_keys=True) + "\n",
    )
    _write_text(destination / "worktree.txt", inputs.worktree_text + "\n")
    return destination


def capture_evidence(repository: Path, options: CaptureOptions) -> Path:
    """Capture fixed fixtures and gate evidence for the current Git SHA."""
    output_root = ensure_output_root(repository, options.output_root)
    branch_status = _run_git(repository, "status", "--short", "--branch")
    branch_line = branch_status.splitlines()[0] if branch_status else ""
    branch = branch_line.removeprefix("## ").split("...", maxsplit=1)[0]
    if not branch:
        raise CaptureError("Git branch is unavailable")
    bot_sha = _validate_sha(_run_git(repository, "rev-parse", "HEAD"))
    worktree_clean = _validate_worktree(
        branch_status, options.allow_explained_paths
    )
    focused_log = _read_text(options.focused_log, label="focused log")
    full_gate_log = _read_text(options.full_gate_log, label="full gate log")
    focused_exit = parse_gate_log(focused_log, expected_sha=bot_sha)
    full_exit = parse_gate_log(full_gate_log, expected_sha=bot_sha)
    matrix_value = _read_json(options.python_matrix, label="Python matrix")
    matrix = parse_python_matrix(matrix_value)
    evidence = AcceptanceEvidence(
        bot_sha=bot_sha,
        branch=branch,
        worktree_clean=worktree_clean,
        hashes=ContractHashes(
            fixture_sha256=_hash_contracts(repository, A2UI_FIXTURES),
            http_body_sha256=_hash_contracts(repository, HTTP_GOLDENS),
        ),
        gates=GateEvidence(
            focused_exit_code=focused_exit,
            full_gate_exit_code=full_exit,
            python_matrix=matrix,
        ),
        generated_at=datetime.now(UTC).isoformat(),
    )
    return write_artifact(
        repository,
        output_root,
        evidence,
        ArtifactInputs(
            focused_log=focused_log,
            full_gate_log=full_gate_log,
            python_matrix=matrix,
            worktree_text=branch_status,
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the mutation-free evidence capture CLI."""
    parser = argparse.ArgumentParser(
        description="Capture current-SHA sanitized Bot contract evidence."
    )
    parser.add_argument("--focused-log", type=Path, required=True)
    parser.add_argument("--full-gate-log", type=Path, required=True)
    parser.add_argument("--python-matrix", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--allow-explained-path",
        action="append",
        default=[],
        help="Allow one explicitly documented dirty worktree path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run evidence capture and return a safe CLI status."""
    args = build_parser().parse_args(argv)
    options = CaptureOptions(
        focused_log=args.focused_log,
        full_gate_log=args.full_gate_log,
        python_matrix=args.python_matrix,
        output_root=args.output_root,
        allow_explained_paths=tuple(args.allow_explained_path),
    )
    try:
        destination = capture_evidence(_ROOT, options)
    except CaptureError as exc:
        print(f"contract evidence rejected: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
