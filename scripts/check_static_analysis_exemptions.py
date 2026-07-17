#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""CLI for static-analysis exemption inventory and reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

if TYPE_CHECKING:
    from scripts.static_analysis.collectors.errors import CollectionError
    from scripts.static_analysis.collectors.pylint import (
        parse_pylint_json,
        run_cross_file_pylint,
        run_full_pylint,
        tracked_python_files,
        tracked_python_files_with_stubs,
    )
    from scripts.static_analysis.inventory import (
        AuditResult,
        collect_inventory,
        reconcile,
    )
    from scripts.static_analysis.model import (
        Classification,
        Registry,
        RegistryError,
        load_registry,
    )
    from scripts.static_analysis.report import (
        render_candidates,
        render_json,
        render_markdown,
        render_repository_markdown,
        render_review,
    )
else:
    _MODULE_PREFIX = (
        "scripts.static_analysis" if __package__ else "static_analysis"
    )
    _errors = import_module(f"{_MODULE_PREFIX}.collectors.errors")
    _pylint = import_module(f"{_MODULE_PREFIX}.collectors.pylint")
    _inventory = import_module(f"{_MODULE_PREFIX}.inventory")
    _model = import_module(f"{_MODULE_PREFIX}.model")
    _report = import_module(f"{_MODULE_PREFIX}.report")
    CollectionError = _errors.CollectionError
    parse_pylint_json = _pylint.parse_pylint_json
    run_cross_file_pylint = _pylint.run_cross_file_pylint
    run_full_pylint = _pylint.run_full_pylint
    tracked_python_files = _pylint.tracked_python_files
    tracked_python_files_with_stubs = _pylint.tracked_python_files_with_stubs
    AuditResult = _inventory.AuditResult
    collect_inventory = _inventory.collect_inventory
    reconcile = _inventory.reconcile
    Classification = _model.Classification
    Registry = _model.Registry
    RegistryError = _model.RegistryError
    load_registry = _model.load_registry
    render_candidates = _report.render_candidates
    render_json = _report.render_json
    render_markdown = _report.render_markdown
    render_repository_markdown = _report.render_repository_markdown
    render_review = _report.render_review

_ROOT = _PROJECT_ROOT
_REGISTRY = _ROOT / "static-analysis-exemptions.toml"
_CROSS_FILE_RULES = frozenset({("pylint", "R0801"), ("pylint", "R0903")})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "inventory"):
        command = subparsers.add_parser(name)
        command.add_argument("--scope", default="full")
        command.add_argument("--python-version")
        command.add_argument("--tool", action="append", default=[])
        command.add_argument("--rule", action="append", default=[])
        command.add_argument("--classification", action="append", default=[])
        command.add_argument("--relationship", action="append", default=[])
        command.add_argument(
            "--format",
            choices=("json", "review", "toml"),
            default="json" if name == "check" else "review",
        )
        command.add_argument("--registry", type=Path, default=_REGISTRY)
        if name == "check":
            command.add_argument(
                "--require-zero-temporary", action="store_true"
            )
        else:
            command.add_argument(
                "--candidate-classification",
                choices=("temporary", "structural"),
            )
            command.add_argument("--candidate-expires-on")
    pylint = subparsers.add_parser(
        "check-pylint",
        help="run Pylint and reconcile its exact cross-file findings",
    )
    pylint.add_argument(
        "--python-version",
        choices=("3.12", "3.13", "3.14"),
        required=True,
    )
    files = pylint.add_mutually_exclusive_group(required=True)
    files.add_argument("--files-from-git", action="store_true")
    files.add_argument("--files-from-stdin", action="store_true")
    pylint.add_argument("--cross-files-from-git", action="store_true")
    pylint.add_argument("--registry", type=Path, default=_REGISTRY)
    render = subparsers.add_parser("render-docs")
    render.add_argument("--registry", type=Path, default=_REGISTRY)
    render.add_argument("--output", type=Path, default=None)
    render.add_argument("--check", action="store_true")
    return parser


def _selector_values(values: list[str]) -> frozenset[str]:
    return frozenset(
        item.strip().lower()
        for value in values
        for item in value.split(",")
        if item.strip()
    )


def _area(path: str) -> str:
    """Map repository paths to stable selector relationship families."""
    if path.startswith("src/"):
        return "production"
    if path.startswith(("tests/", "e2e/")):
        return "test"
    if path.startswith("scripts/") or path == "Makefile":
        return "tool"
    return "other"


def _relationships(item: object) -> frozenset[str]:
    path = getattr(item, "path", "")
    peer_path = getattr(item, "peer_path", None)
    left = _area(path)
    if not peer_path:
        return frozenset({left})
    right = _area(peer_path)
    return frozenset({"-".join(sorted((left, right)))})


def _selected(item: object, args: argparse.Namespace) -> bool:
    tools = _selector_values(args.tool)
    rules = _selector_values(args.rule)
    classifications = _selector_values(args.classification)
    relationships = _selector_values(args.relationship)
    if tools and getattr(item, "tool", "").lower() not in tools:
        return False
    if rules and getattr(item, "rule", "").lower() not in rules:
        return False
    if classifications:
        value = getattr(item, "classification", None)
        value = getattr(value, "value", value)
        if str(value).lower() not in classifications:
            return False
    if relationships:
        mechanism = getattr(getattr(item, "mechanism", None), "value", "")
        target = getattr(getattr(item, "target_kind", None), "value", "")
        available = {mechanism.lower(), target.lower()}
        available.update(_relationships(item))
        if not available.intersection(relationships):
            return False
    return True


def _filter_result(
    result: AuditResult, args: argparse.Namespace
) -> AuditResult:
    return AuditResult(
        matched=tuple(
            item for item in result.matched if _selected(item, args)
        ),
        unregistered=tuple(
            item for item in result.unregistered if _selected(item, args)
        ),
        stale=tuple(item for item in result.stale if _selected(item, args)),
        duplicate_findings=tuple(
            item for item in result.duplicate_findings if _selected(item, args)
        ),
        duplicate_exemptions=tuple(
            item
            for item in result.duplicate_exemptions
            if _selected(item, args)
        ),
        wildcard_findings=tuple(
            item for item in result.wildcard_findings if _selected(item, args)
        ),
        expired=tuple(
            item for item in result.expired if _selected(item, args)
        ),
    )


def _registry_for_scope(registry: Registry, scope: str) -> Registry:
    """Limit partial reconciliation to the findings that scope observes."""
    if scope != "cross-file":
        return registry
    return replace(
        registry,
        exemptions=tuple(
            item
            for item in registry.exemptions
            if (item.tool, item.rule) in _CROSS_FILE_RULES
            and item.mechanism.value == "diagnostic"
        ),
    )


def _validated_python_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Validate a caller-supplied Python path list against Git tracking."""
    if not paths:
        raise CollectionError("no Python files were supplied")
    tracked = set(tracked_python_files(_ROOT))
    if len(set(paths)) != len(paths):
        raise CollectionError("stdin Python file list contains duplicates")
    invalid = tuple(path for path in paths if path not in tracked)
    if invalid:
        raise CollectionError(
            "stdin Python file list contains untracked paths: "
            + ", ".join(invalid)
        )
    return paths


def _stdin_python_paths() -> tuple[str, ...]:
    """Read a strict NUL-delimited tracked Python path list from stdin."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    payload = stream.read()
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, bytes):
        raise CollectionError("stdin Python file list is not byte data")
    parts = payload.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if not parts or any(not part for part in parts):
        raise CollectionError("stdin Python file list is empty or malformed")
    try:
        paths = tuple(part.decode("utf-8") for part in parts)
    except UnicodeDecodeError as exc:
        raise CollectionError("stdin Python file list is not UTF-8") from exc
    if any(not path.endswith(".py") for path in paths):
        raise CollectionError(
            "stdin Python file list contains non-Python paths"
        )
    return _validated_python_paths(paths)


def _cross_records(
    root: Path, records: tuple[dict[str, object], ...], version: str
) -> tuple:
    """Parse the bounded cross-file records from a full JSON report."""
    selected = [
        record
        for record in records
        if record.get("message-id") in {"R0801", "R0903"}
    ]
    return parse_pylint_json(root, json.dumps(selected), version)


def _pylint_diagnostic_lines(
    records: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    """Render ordinary Pylint records without trusting their message text."""
    lines: list[str] = []
    for record in records:
        path = record.get("path", "<unknown>")
        line = record.get("line", "?")
        column = record.get("column", "?")
        rule = record.get("message-id", "<unknown>")
        message = record.get("message", "<missing message>")
        lines.append(f"{path}:{line}:{column}: {rule}: {message}")
    return tuple(lines)


def _check_pylint(args: argparse.Namespace) -> int:
    """Run full Pylint and reconcile only exact cross-file exceptions."""
    if args.files_from_git:
        files = tracked_python_files(_ROOT)
    else:
        files = _stdin_python_paths()
    if not files:
        raise CollectionError("Git returned no tracked Python files")
    version, records = run_full_pylint(
        _ROOT,
        files,
        args.python_version,
    )
    cross_rules = {"R0801", "R0903"}
    cross_records = tuple(
        record for record in records if record.get("message-id") in cross_rules
    )
    cross_findings = _cross_records(_ROOT, cross_records, version)

    if args.cross_files_from_git:
        if not args.files_from_stdin:
            raise CollectionError(
                "--cross-files-from-git requires --files-from-stdin"
            )
        # The focused pass is the only source of truth for exact identities;
        # the changed-file copies above are parsed before they are discarded.
        ordinary = tuple(
            record
            for record in records
            if record.get("message-id") not in cross_rules
        )
    elif args.files_from_stdin:
        # Without the focused pass, a changed-file cross-file diagnostic is an
        # ordinary failure rather than an implicitly authorized exception.
        ordinary = records
        cross_findings = ()
    else:
        ordinary = tuple(
            record
            for record in records
            if record.get("message-id") not in cross_rules
        )

    if ordinary:
        print(
            "check-pylint: ordinary diagnostics are not registered "
            "exemptions",
            file=sys.stderr,
        )
        for line in _pylint_diagnostic_lines(ordinary):
            print(line, file=sys.stderr)
        return 1

    if args.cross_files_from_git or args.files_from_git:
        cross_findings = run_cross_file_pylint(
            _ROOT,
            tracked_python_files_with_stubs(_ROOT),
            args.python_version,
        )

    if not (args.files_from_git or args.cross_files_from_git):
        print(
            "check-pylint: no ordinary diagnostics; cross-file checks were "
            "not requested",
        )
        return 0

    registry = load_registry(args.registry, today=date.today())
    result = reconcile(
        _registry_for_scope(registry, "cross-file"),
        cross_findings,
        date.today(),
    )
    if not result.is_clean:
        sys.stdout.write(render_review(result))
        return 1
    print(
        "check-pylint: exact cross-file findings are registered "
        f"({len(cross_findings)} records; {version})"
    )
    return 0


def _parse_candidate_flags(
    args: argparse.Namespace,
) -> tuple[Classification, date | None] | None:
    classification = args.candidate_classification
    expires_raw = args.candidate_expires_on
    if classification is None and expires_raw is None:
        return None
    if args.format != "toml":
        raise CollectionError(
            "candidate flags are only valid with --format toml"
        )
    if classification is None:
        raise CollectionError("--candidate-classification is required")
    expires = date.fromisoformat(expires_raw) if expires_raw else None
    selected = Classification(classification)
    if selected is Classification.TEMPORARY and expires is None:
        raise CollectionError(
            "temporary candidates require --candidate-expires-on"
        )
    if selected is Classification.STRUCTURAL and expires is not None:
        raise CollectionError("structural candidates may not expire")
    return selected, expires


def _render_inventory(
    findings: tuple,
    args: argparse.Namespace,
    candidate: tuple[Classification, date | None] | None,
) -> str:
    if candidate is not None:
        classification, expires = candidate
        return render_candidates(findings, classification, expires)
    if args.format == "toml":
        raise CollectionError("--format toml requires candidate flags")
    result = AuditResult(
        matched=findings,
        unregistered=findings,
        stale=(),
        duplicate_findings=(),
        duplicate_exemptions=(),
        wildcard_findings=(),
        expired=(),
    )
    return (
        render_json(result) if args.format == "json" else render_review(result)
    )


def _run(args: argparse.Namespace) -> int:
    today = date.today()
    if args.command == "check-pylint":
        return _check_pylint(args)
    if args.command == "render-docs":
        if args.registry != _REGISTRY:
            registry = load_registry(args.registry, today=today)
            content = render_markdown(registry, {})
        else:
            content = render_repository_markdown(_ROOT)
        output = args.output or (_ROOT / "docs/development/lint-exemptions.md")
        if args.check:
            return 0 if output.read_text(encoding="utf-8") == content else 1
        output.write_text(content, encoding="utf-8")
        return 0

    candidate = (
        _parse_candidate_flags(args) if args.command == "inventory" else None
    )
    findings = collect_inventory(_ROOT, args.scope, args.python_version)
    selected_findings = tuple(
        item for item in findings if _selected(item, args)
    )
    if args.command == "inventory":
        sys.stdout.write(_render_inventory(selected_findings, args, candidate))
        return 0

    registry = load_registry(args.registry, today=today)
    effective_registry = _registry_for_scope(registry, args.scope)
    result = reconcile(effective_registry, findings, today)
    filtered = _filter_result(result, args)
    if args.require_zero_temporary and any(
        item.classification is Classification.TEMPORARY
        for item in effective_registry.exemptions
        if _selected(item, args)
    ):
        filtered = AuditResult(
            matched=filtered.matched,
            unregistered=filtered.unregistered,
            stale=filtered.stale,
            duplicate_findings=filtered.duplicate_findings,
            duplicate_exemptions=filtered.duplicate_exemptions,
            wildcard_findings=filtered.wildcard_findings,
            expired=filtered.expired
            + tuple(
                item
                for item in effective_registry.exemptions
                if item.classification is Classification.TEMPORARY
                and _selected(item, args)
            ),
        )
    if args.format == "review":
        sys.stdout.write(render_review(filtered))
    else:
        sys.stdout.write(render_json(filtered) + "\n")
    return 0 if filtered.is_clean else 1


def main(argv: list[str] | None = None) -> int:
    """Run the checker and convert all policy/tool errors to exit 2."""
    try:
        return _run(_parser().parse_args(argv))
    except (CollectionError, RegistryError, OSError, ValueError) as exc:
        print(f"static-analysis-exemptions: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
