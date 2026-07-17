#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""CLI for static-analysis exemption inventory and reconciliation."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

if TYPE_CHECKING:
    from scripts.static_analysis.collectors.errors import CollectionError
    from scripts.static_analysis.inventory import (
        AuditResult,
        collect_inventory,
        reconcile,
    )
    from scripts.static_analysis.model import (
        Classification,
        RegistryError,
        load_registry,
    )
    from scripts.static_analysis.report import (
        render_candidates,
        render_json,
        render_markdown,
        render_review,
    )
else:
    _MODULE_PREFIX = (
        "scripts.static_analysis" if __package__ else "static_analysis"
    )
    _errors = import_module(f"{_MODULE_PREFIX}.collectors.errors")
    _inventory = import_module(f"{_MODULE_PREFIX}.inventory")
    _model = import_module(f"{_MODULE_PREFIX}.model")
    _report = import_module(f"{_MODULE_PREFIX}.report")
    CollectionError = _errors.CollectionError
    AuditResult = _inventory.AuditResult
    collect_inventory = _inventory.collect_inventory
    reconcile = _inventory.reconcile
    Classification = _model.Classification
    RegistryError = _model.RegistryError
    load_registry = _model.load_registry
    render_candidates = _report.render_candidates
    render_json = _report.render_json
    render_markdown = _report.render_markdown
    render_review = _report.render_review

_ROOT = _PROJECT_ROOT
_REGISTRY = _ROOT / "static-analysis-exemptions.toml"


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
    if args.command == "render-docs":
        registry = load_registry(args.registry, today=today)
        content = render_markdown(registry, {})
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
    result = reconcile(registry, findings, today)
    filtered = _filter_result(result, args)
    if args.require_zero_temporary and any(
        item.classification is Classification.TEMPORARY
        for item in registry.exemptions
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
                for item in registry.exemptions
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
