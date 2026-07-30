# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fail-closed collection of cross-file Pylint diagnostics."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..fingerprints import (
    Endpoint,
    containing_symbol,
    finding_fingerprint,
    normalize_source,
)
from ..model import Finding, Mechanism, TargetKind
from .errors import CollectionError, ReverseEvidence
from .helpers import FindingParts, make_finding
from .process import run_command, tracked_git_files

_RULES = frozenset({"R0801", "R0903"})
_FULL_STATUS_BITS = 2 | 4 | 8 | 16
_ENDPOINT_RE = re.compile(
    r"^==(?P<module>[^:\n]+):\[(?P<start>\d+):(?P<end>\d+)\]$",
    re.MULTILINE,
)
_PYLINT_VERSION = ("uv", "run", "pylint", "--version")


@dataclass(frozen=True)
class _PylintPathContext:
    """Tracked-source context shared while resolving Pylint endpoints."""

    root: Path
    tracked: Sequence[Path]


def validate_pylint_result(returncode: int, stdout: str, stderr: str) -> str:
    """Validate Pylint's bounded exit status and JSON output."""
    if returncode not in {0, 8}:
        detail = stderr.strip() or stdout.strip() or "no output"
        raise CollectionError(
            f"pylint invocation failed with status {returncode}: {detail}"
        )
    output = stdout.strip()
    if not output:
        detail = stderr.strip() or "no output"
        raise CollectionError(f"pylint returned empty JSON output: {detail}")
    try:
        document: Any = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CollectionError("pylint returned malformed JSON") from exc
    if not isinstance(document, list):
        raise CollectionError("pylint JSON output must be an array")
    if any(not isinstance(item, dict) for item in document):
        raise CollectionError("pylint JSON diagnostics must be objects")
    return output


def validate_full_pylint_result(
    returncode: int, stdout: str, stderr: str
) -> str:
    """Validate a full Pylint report without hiding diagnostic status bits.

    Pylint combines ordinary diagnostic categories into a bit mask.  The
    fatal (bit 1) and usage-error (bit 32) bits indicate that the invocation
    itself was not trustworthy, so only the diagnostic bits are accepted here;
    their records are still evaluated by the caller.
    """
    if returncode < 0 or returncode & ~_FULL_STATUS_BITS:
        detail = stderr.strip() or stdout.strip() or "no output"
        raise CollectionError(
            f"pylint invocation failed with unexpected status {returncode}: "
            f"{detail}"
        )
    output = stdout.strip()
    if not output:
        detail = stderr.strip() or "no output"
        raise CollectionError(f"pylint returned empty JSON output: {detail}")
    try:
        document: Any = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CollectionError("pylint returned malformed JSON") from exc
    if not isinstance(document, list):
        raise CollectionError("pylint JSON diagnostics must be an array")
    for record in document:
        if not isinstance(record, dict):
            raise CollectionError("pylint JSON diagnostics must be objects")
        message_id = record.get("message-id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise CollectionError("pylint JSON diagnostic lacks a message-id")
    return output


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def tracked_python_files(root: Path) -> tuple[str, ...]:
    """Return tracked implementation Python paths in Git order."""
    return tuple(
        _relative_path(root, path)
        for path in tracked_git_files(root, ("*.py", "*.pyi"))
        if path.suffix == ".py"
    )


def tracked_python_files_with_stubs(root: Path) -> tuple[str, ...]:
    """Return tracked ``.py`` and ``.pyi`` paths for exact diagnostics."""
    return tuple(
        _relative_path(root, path)
        for path in tracked_git_files(root, ("*.py", "*.pyi"))
    )


def _module_name(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    value = relative.with_suffix("").as_posix().replace("/", ".")
    if value.startswith("src."):
        value = value[4:]
    if value.endswith(".__init__"):
        value = value[: -len(".__init__")]
    return value


def _candidate_paths(root: Path, paths: Sequence[Path]) -> str:
    """Render sorted candidate paths relative to the analysis root."""
    return ", ".join(sorted(_relative_path(root, path) for path in paths))


def _normalize_span_source(source: str) -> str:
    """Normalize complete or partial Pylint source spans."""
    try:
        return normalize_source(source)
    except ValueError:
        dedented = textwrap.dedent(source)
        try:
            return normalize_source(dedented)
        except ValueError:
            return dedented.strip()


def _endpoint_candidates(
    root: Path, module: str, tracked: Sequence[Path]
) -> tuple[Path, ...]:
    """Return every tracked path that could represent one endpoint label."""
    module_names = {module}
    if module.endswith(".__init__"):
        module_names.add(module[: -len(".__init__")])
    suffix = f".{module}"
    direct = root / f"{module.replace('.', '/')}.py"
    return tuple(
        path
        for path in tracked
        if _module_name(root, path) in module_names
        or _module_name(root, path).endswith(suffix)
        or path == direct
        or path.stem == module
    )


def _resolve_module(
    context: _PylintPathContext,
    module: str,
    start: int,
    end: int,
    source_hint: str | None,
) -> Path:
    """Resolve one Pylint endpoint using path and source evidence."""
    root = context.root
    tracked = context.tracked
    module_names = {module}
    if module.endswith(".__init__"):
        module_names.add(module[: -len(".__init__")])
    exact_matches = tuple(
        path for path in tracked if _module_name(root, path) in module_names
    )
    if len(exact_matches) == 1:
        return exact_matches[0]
    suffix = f".{module}"
    suffix_matches = tuple(
        path for path in tracked if _module_name(root, path).endswith(suffix)
    )
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    direct = root / f"{module.replace('.', '/')}.py"
    if direct in tracked and direct.is_file():
        return direct
    candidates = (
        exact_matches
        or suffix_matches
        or tuple(path for path in tracked if path.stem == module)
    )
    reported_candidates = candidates
    if source_hint is not None:
        candidates = tuple(
            path
            for path in candidates
            if source_hint in _span_source(path, start, end)
        )
    if len(candidates) == 1:
        return candidates[0]
    detail = _candidate_paths(root, candidates or reported_candidates)
    raise CollectionError(
        f"cannot resolve Pylint module {module!r}; candidates: {detail}"
    )


def _span_source(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if start < 1 or end < start or end > len(lines):
        raise CollectionError(
            f"Pylint span is outside {path.as_posix()}: {start}:{end}"
        )
    snippet = "\n".join(lines[slice(start - 1, end)])
    return _normalize_span_source(snippet)


def _span_endpoint(
    context: _PylintPathContext,
    module: str,
    start: int,
    end: int,
    source_hint: str | None,
) -> tuple[Path, Endpoint, str]:
    path = _resolve_module(context, module, start, end, source_hint)
    root = context.root
    relative = _relative_path(root, path)
    normalized = _span_source(path, start, end)
    return path, Endpoint(relative, f"{start}:{end}", normalized), normalized


def _pair_endpoints(
    root: Path, message: str, tracked: Sequence[Path]
) -> tuple[Path, Endpoint, Path, Endpoint]:
    matches = tuple(_ENDPOINT_RE.finditer(message))
    if len(matches) != 2:
        raise CollectionError(
            "R0801 diagnostic must contain exactly two source endpoints"
        )
    first_match, second_match = matches
    source = message[slice(second_match.end(), None)]
    source_hint = _normalize_span_source(source) if source.strip() else None
    context = _PylintPathContext(root, tracked)
    first = _span_endpoint(
        context,
        first_match.group("module"),
        int(first_match.group("start")),
        int(first_match.group("end")),
        source_hint,
    )
    second = _span_endpoint(
        context,
        second_match.group("module"),
        int(second_match.group("start")),
        int(second_match.group("end")),
        source_hint,
    )
    if first[0] == second[0]:
        candidates = tuple(
            dict.fromkeys(
                _endpoint_candidates(
                    root, first_match.group("module"), tracked
                )
                + _endpoint_candidates(
                    root, second_match.group("module"), tracked
                )
            )
        )
        raise CollectionError(
            "R0801 endpoints did not resolve to distinct paths; "
            f"candidates: {_candidate_paths(root, candidates)}"
        )
    return first[0], first[1], second[0], second[1]


def _pair_finding(
    root: Path,
    record: dict[str, Any],
    message: str,
    version: str,
    tracked: Sequence[Path],
) -> Finding:
    first_path, first_endpoint, second_path, second_endpoint = _pair_endpoints(
        root, message, tracked
    )
    line = record.get("line")
    if not isinstance(line, int):
        raise CollectionError("R0801 diagnostic is missing an integer line")
    first_relative = _relative_path(root, first_path)
    second_relative = _relative_path(root, second_path)
    return Finding(
        tool="pylint",
        rule="R0801",
        mechanism=Mechanism.DIAGNOSTIC,
        target_kind=TargetKind.PAIR,
        path=first_relative,
        symbol=first_endpoint.symbol,
        peer_path=second_relative,
        peer_symbol=second_endpoint.symbol,
        fingerprint=finding_fingerprint(
            "pylint",
            "R0801",
            Mechanism.DIAGNOSTIC,
            (first_endpoint, second_endpoint),
            "duplicate-code",
        ),
        location=f"{first_relative}:{line}",
        message=message,
        tool_version=version,
    )


def _class_source(path: Path, line: int, symbol: str) -> str:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CollectionError(f"cannot parse Pylint target {path}") from exc
    class_name = symbol.rsplit(".", maxsplit=1)[-1]
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
        and node.lineno == line
    ]
    if len(matches) != 1:
        raise CollectionError(
            f"cannot resolve R0903 class {symbol!r} at {path}:{line}"
        )
    segment = ast.get_source_segment(source, matches[0])
    if not segment:
        raise CollectionError(f"R0903 class source is empty: {path}:{line}")
    return normalize_source(segment)


def _symbol_finding(
    root: Path,
    record: dict[str, Any],
    version: str,
) -> Finding:
    raw_path = record.get("path")
    raw_line = record.get("line")
    raw_symbol = record.get("obj")
    if not isinstance(raw_path, str) or not isinstance(raw_line, int):
        raise CollectionError("R0903 diagnostic lacks path or line")
    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        raise CollectionError("R0903 diagnostic lacks class symbol")
    path = root / raw_path
    if not path.is_file():
        raise CollectionError(f"R0903 path does not exist: {raw_path}")
    symbol = containing_symbol(path.read_text(encoding="utf-8"), raw_line)
    if symbol is None or not symbol.endswith(raw_symbol.rsplit(".", 1)[-1]):
        raise CollectionError(
            f"R0903 symbol mismatch at {raw_path}:{raw_line}: {raw_symbol}"
        )
    normalized = _class_source(path, raw_line, raw_symbol)
    message = record.get("message")
    if not isinstance(message, str) or not message.strip():
        raise CollectionError("R0903 diagnostic lacks message")
    relative = _relative_path(root, path)
    parts: FindingParts = {
        "tool": "pylint",
        "rule": "R0903",
        "mechanism": Mechanism.DIAGNOSTIC,
        "target_kind": TargetKind.SYMBOL,
        "path": relative,
        "symbol": symbol,
        "peer_path": None,
        "peer_symbol": None,
        "location": f"{relative}:{raw_line}",
        "message": message,
        "source": normalized,
    }
    finding = make_finding(parts)
    return replace(finding, tool_version=version)


def parse_pylint_json(
    root: Path, text: str, version: str
) -> tuple[Finding, ...]:
    """Parse only the two explicitly bounded cross-file Pylint rules."""
    try:
        document: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectionError("Pylint JSON is malformed") from exc
    if not isinstance(document, list):
        raise CollectionError("Pylint JSON diagnostics must be an array")
    tracked = tracked_git_files(root, ("*.py", "*.pyi"))
    findings: list[Finding] = []
    for record in document:
        if not isinstance(record, dict):
            raise CollectionError("Pylint diagnostic must be an object")
        rule = record.get("message-id")
        if not isinstance(rule, str) or rule not in _RULES:
            raise CollectionError(f"unknown Pylint rule {rule!r}")
        message = record.get("message")
        if not isinstance(message, str):
            raise CollectionError(f"Pylint {rule} diagnostic lacks message")
        if rule == "R0801":
            findings.append(
                _pair_finding(root, record, message, version, tracked)
            )
        else:
            findings.append(_symbol_finding(root, record, version))
    return tuple(findings)


def _run(
    root: Path, command: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    return run_command(
        command,
        root,
        error_message="Pylint executable failed",
    )


def run_cross_file_pylint(
    root: Path,
    files: Sequence[str],
    python_version: str | None,
) -> tuple[Finding, ...]:
    """Run bounded Pylint and return exact R0801/R0903 findings."""
    version_result = _run(root, _PYLINT_VERSION)
    if version_result.returncode != 0:
        raise CollectionError(
            f"Pylint version command failed: {version_result.stderr.strip()}"
        )
    version = (version_result.stdout or version_result.stderr).strip()
    if not version:
        raise CollectionError("Pylint version command returned empty output")
    command = [
        "uv",
        "run",
        "pylint",
        "--persistent=no",
        "--output-format=json",
        "--disable=all",
        "--enable=R0801,R0903",
    ]
    if python_version is not None:
        command.extend(("--py-version", python_version))
    command.extend(files)
    result = _run(root, command)
    text = validate_pylint_result(
        result.returncode, result.stdout, result.stderr
    )
    return parse_pylint_json(root, text, version.splitlines()[0])


def run_full_pylint(
    root: Path,
    files: Sequence[str],
    python_version: str,
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Run all Pylint rules and return its validated JSON diagnostics."""
    version_result = _run(root, _PYLINT_VERSION)
    if version_result.returncode != 0:
        raise CollectionError(
            f"Pylint version command failed: {version_result.stderr.strip()}"
        )
    version = (version_result.stdout or version_result.stderr).strip()
    if not version:
        raise CollectionError("Pylint version command returned empty output")
    command = [
        "uv",
        "run",
        "pylint",
        "--persistent=no",
        "--output-format=json",
        "--py-version",
        python_version,
        *files,
    ]
    result = _run(root, command)
    text = validate_full_pylint_result(
        result.returncode, result.stdout, result.stderr
    )
    document = json.loads(text)
    return version.splitlines()[0], tuple(document)


def audit_pylint_suppressions(
    root: Path,
    findings: Sequence[Finding],
    files: Sequence[str],
) -> tuple[ReverseEvidence, ...]:
    """Check Pylint's useless-suppression diagnostics in a bounded run."""
    pylint_findings = tuple(item for item in findings if item.tool == "pylint")
    if not pylint_findings:
        return ()
    command = [
        "uv",
        "run",
        "pylint",
        "--persistent=no",
        "--output-format=json",
        "--enable=useless-suppression",
        "--disable=all",
        "--enable=useless-suppression",
        *files,
    ]
    result = _run(root, command)
    text = validate_pylint_result(
        result.returncode, result.stdout, result.stderr
    )
    try:
        diagnostics: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectionError(
            "Pylint suppression audit returned malformed JSON"
        ) from exc
    if any(
        isinstance(item, dict) and item.get("message-id") == "I0021"
        for item in diagnostics
    ):
        raise CollectionError("Pylint reported a useless suppression")
    version_result = _run(root, _PYLINT_VERSION)
    version = (version_result.stdout or version_result.stderr).strip()
    if version_result.returncode != 0 or not version:
        raise CollectionError("Pylint version command failed during audit")
    evidence = tuple(
        ReverseEvidence(
            finding=item,
            command=tuple(command),
            returncode=result.returncode,
            output=f"{version.splitlines()[0]}\n{text}",
            necessary=True,
        )
        for item in pylint_findings
    )
    return evidence
