# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable report and candidate renderers for exemption audits."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date

from .inventory import AuditResult, _sort_finding
from .model import Classification, Exemption, Finding, Registry


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
    """Render a small deterministic registry ledger.

    Task 8 extends this renderer with the full human review narrative while
    keeping this stable summary available to the checker CLI.
    """
    lines = ["# Static-analysis exemption ledger", ""]
    lines.append(f"- Schema: `{registry.schema_version}`")
    lines.append(f"- Entries: `{len(registry.exemptions)}`")
    for tool in sorted(counts):
        lines.append(f"- {tool}: `{counts[tool]}`")
    lines.append("")
    for item in sorted(registry.exemptions, key=lambda value: value.id):
        lines.append(
            f"- `{item.id}` `{item.tool}:{item.rule}` "
            f"`{item.path}` ({item.classification.value})"
        )
    return "\n".join(lines).rstrip() + "\n"
