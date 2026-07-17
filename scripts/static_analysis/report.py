# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable report and candidate renderers for exemption audits."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

import mdformat
import mdformat.plugins

from .inventory import AuditResult, _sort_finding
from .model import (
    Classification,
    Exemption,
    Finding,
    Registry,
    load_registry,
)


def _finding_dict(item: Finding) -> dict[str, object]:
    return {
        "tool": item.tool,
        "rule": item.rule,
        "mechanism": item.mechanism.value,
        "target_kind": item.target_kind.value,
        "path": item.path,
        "symbol": item.symbol,
        "peer_path": item.peer_path,
        "peer_symbol": item.peer_symbol,
        "fingerprint": item.fingerprint,
        "location": item.location,
        "message": item.message,
        "tool_version": item.tool_version,
    }


def _exemption_dict(item: Exemption) -> dict[str, object]:
    return {
        "id": item.id,
        "tool": item.tool,
        "rule": item.rule,
        "classification": item.classification.value,
        "mechanism": item.mechanism.value,
        "target_kind": item.target_kind.value,
        "path": item.path,
        "symbol": item.symbol,
        "peer_path": item.peer_path,
        "peer_symbol": item.peer_symbol,
        "fingerprint": item.fingerprint,
        "owner": item.owner,
        "introduced_on": item.introduced_on.isoformat(),
        "review_on": item.review_on.isoformat(),
        "rationale": item.rationale,
        "counterfactual": item.counterfactual,
        "risk": item.risk,
        "tests": list(item.tests),
        "expires_on": (
            item.expires_on.isoformat()
            if item.expires_on is not None
            else None
        ),
        "remediation": item.remediation,
    }


def render_json(result: AuditResult) -> str:
    """Render a deterministic machine-readable reconciliation report."""
    document = {
        "clean": result.is_clean,
        "matched": [_finding_dict(item) for item in result.matched],
        "unregistered": [_finding_dict(item) for item in result.unregistered],
        "stale": [_exemption_dict(item) for item in result.stale],
        "duplicate_findings": [
            _finding_dict(item) for item in result.duplicate_findings
        ],
        "duplicate_exemptions": [
            _exemption_dict(item) for item in result.duplicate_exemptions
        ],
        "wildcard_findings": [
            _finding_dict(item) for item in result.wildcard_findings
        ],
        "expired": [_exemption_dict(item) for item in result.expired],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)


def render_review(result: AuditResult) -> str:
    """Render a concise Markdown review report."""
    lines = ["# Static-analysis exemption review", ""]
    lines.append(f"- Clean: `{str(result.is_clean).lower()}`")
    lines.append(f"- Matched: `{len(result.matched)}`")
    lines.append(f"- Unregistered: `{len(result.unregistered)}`")
    lines.append(f"- Stale: `{len(result.stale)}`")
    lines.append(
        f"- Duplicate registry entries: `{len(result.duplicate_exemptions)}`"
    )
    lines.append(f"- Expired: `{len(result.expired)}`")
    lines.append("")
    for title, findings in (
        ("Unregistered findings", result.unregistered),
        ("Wildcard findings", result.wildcard_findings),
        ("Duplicate findings", result.duplicate_findings),
    ):
        lines.extend((f"## {title}", ""))
        if not findings:
            lines.extend(("None.", ""))
            continue
        for item in findings:
            lines.append(
                f"- `{item.tool}:{item.rule}` `{item.path}` "
                f"`{item.fingerprint}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_candidates(
    findings: Iterable[Finding],
    classification: Classification,
    expires_on: date | None,
) -> str:
    """Render deterministic candidate registry TOML."""
    if classification is Classification.TEMPORARY and expires_on is None:
        raise ValueError("temporary candidates require expires_on")
    if classification is Classification.STRUCTURAL and expires_on is not None:
        raise ValueError("structural candidates may not expire")
    lines = ["schema_version = 1", "", "[policy]", 'default = "deny"', ""]
    ordered = sorted(
        findings,
        key=_sort_finding,
    )
    prefix = (
        "SAE-TMP-"
        if classification is Classification.TEMPORARY
        else "SAE-STR-"
    )
    for index, item in enumerate(ordered, start=1):
        lines.extend(("[[exemptions]]", f'id = "{prefix}{index:04d}"'))
        lines.extend(
            (
                f"tool = {_toml_string(item.tool)}",
                f"rule = {_toml_string(item.rule)}",
                f"classification = {_toml_string(classification.value)}",
                f"mechanism = {_toml_string(item.mechanism.value)}",
                f"target_kind = {_toml_string(item.target_kind.value)}",
                f"path = {_toml_string(item.path)}",
            )
        )
        if item.symbol is not None:
            lines.append(f"symbol = {_toml_string(item.symbol)}")
        if item.peer_path is not None:
            lines.append(f"peer_path = {_toml_string(item.peer_path)}")
        if item.peer_symbol is not None:
            lines.append(f"peer_symbol = {_toml_string(item.peer_symbol)}")
        lines.extend(
            (
                f"fingerprint = {_toml_string(item.fingerprint)}",
                'owner = "bot-maintainers"',
                "introduced_on = 2026-07-17",
                "review_on = 2026-08-15",
                f"rationale = {_toml_string(item.message)}",
                'counterfactual = "Remove or refactor after review."',
                'risk = "Suppression can hide a future regression."',
                'tests = ["static-analysis-inventory"]',
            )
        )
        if expires_on is not None:
            lines.append(f"expires_on = {expires_on.isoformat()}")
            lines.append('remediation = "SAE-WORK-INITIAL-AUDIT"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(registry: Registry, counts: Mapping[str, int]) -> str:
    """Render the complete deterministic human-review ledger."""
    lines = [
        "# Static-analysis exemption ledger",
        "",
        "This file is generated from `static-analysis-exemptions.toml`.",
        "",
        "Regeneration:",
        "",
        "```bash",
        "uv run python scripts/check_static_analysis_exemptions.py "
        "render-docs",
        "```",
        "",
        f"- Schema version: `{registry.schema_version}`",
        f"- Policy default: `{registry.default}`",
        f"- Authorized records: `{len(registry.exemptions)}`",
        "",
        "## Informational counts",
        "",
        "| Tool and rule | Records |",
        "| --- | ---: |",
    ]
    for key in sorted(counts):
        lines.append(f"| `{_md_cell(key)}` | {counts[key]} |")
    lines.extend(
        (
            "",
            "## Exact records",
            "",
            "".join(
                (
                    "| ID | Tool | Rule | Classification | ",
                    "Mechanism | Target | Path | Symbol | ",
                    "Fingerprint | Owner | Introduced | Review | ",
                    "Expiry | Remediation | Tests |",
                )
            ),
            "".join(
                (
                    "| --- | --- | --- | --- | --- | --- | --- | --- | ",
                    "--- | --- | --- | --- | --- | --- | --- | --- |",
                )
            ),
        )
    )
    for item in sorted(registry.exemptions, key=lambda value: value.id):
        expiry = item.expires_on.isoformat() if item.expires_on else "—"
        remediation = item.remediation or "—"
        tests = ", ".join(item.tests)
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{_md_cell(item.id)}`",
                    _md_cell(item.tool),
                    _md_cell(item.rule),
                    _md_cell(item.classification.value),
                    _md_cell(item.mechanism.value),
                    _md_cell(item.target_kind.value),
                    _md_cell(item.path),
                    _md_cell(item.symbol or "—"),
                    f"`{_md_cell(item.fingerprint)}`",
                    _md_cell(item.owner),
                    item.introduced_on.isoformat(),
                    item.review_on.isoformat(),
                    _md_cell(expiry),
                    _md_cell(remediation),
                    _md_cell(tests),
                )
            )
            + " |"
        )
    lines.extend(("", "## Review fields", ""))
    for item in sorted(registry.exemptions, key=lambda value: value.id):
        lines.extend(
            (
                f"### `{_md_cell(item.id)}`",
                "",
                "Rationale:",
                "",
                *_code_block(item.rationale),
                "Counterfactual:",
                "",
                *_code_block(item.counterfactual),
                "Risk:",
                "",
                *_code_block(item.risk),
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _md_cell(value: str) -> str:
    """Escape a value for a single Markdown table/list cell."""
    return value.replace("|", "\\|").replace("\n", " ↵ ")


def _code_block(value: str) -> tuple[str, ...]:
    """Render arbitrary registry prose without Markdown interpretation."""
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", value)),
        default=0,
    )
    fence = "`" * max(3, longest + 1)
    return (f"{fence}text", value, fence)


def render_repository_markdown(root: Path) -> str:
    """Load the repository registry and render its deterministic ledger."""
    registry = load_registry(
        root / "static-analysis-exemptions.toml", today=date.today()
    )
    counts = Counter(
        f"{item.tool}:{item.rule}" for item in registry.exemptions
    )
    return mdformat.text(
        render_markdown(registry, counts),
        options={"wrap": "keep"},
        extensions=mdformat.plugins.PARSER_EXTENSIONS,
        codeformatters=mdformat.plugins.CODEFORMATTERS,
        _filename=str(root / "docs/development/lint-exemptions.md"),
    )
