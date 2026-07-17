# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Repository-wide finding collection and registry reconciliation."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .collectors.ci import collect_ci_suppressions
from .collectors.config import collect_config_suppressions
from .collectors.errors import CollectionError
from .collectors.pylint import run_cross_file_pylint
from .collectors.secrets import collect_secret_markers
from .collectors.source import collect_source_suppressions
from .collectors.warnings import collect_warning_suppressions
from .model import Exemption, Finding, Registry


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Deterministic comparison between observed findings and the registry."""

    matched: tuple[Finding, ...]
    unregistered: tuple[Finding, ...]
    stale: tuple[Exemption, ...]
    duplicate_findings: tuple[Finding, ...]
    duplicate_exemptions: tuple[Exemption, ...]
    wildcard_findings: tuple[Finding, ...]
    expired: tuple[Exemption, ...]

    @property
    def is_clean(self) -> bool:
        """Whether every selected finding is authorized and current."""
        return not (
            self.unregistered
            or self.stale
            or self.duplicate_findings
            or self.duplicate_exemptions
            or self.wildcard_findings
            or self.expired
        )


def _identity(item: Finding | Exemption) -> tuple[object, ...]:
    return (
        item.tool,
        item.rule,
        item.mechanism,
        item.target_kind,
        item.path,
        item.symbol,
        item.peer_path,
        item.peer_symbol,
        item.fingerprint,
    )


def _sort_finding(item: Finding) -> tuple[object, ...]:
    return (
        item.tool,
        item.rule,
        item.path,
        item.symbol or "",
        item.peer_path or "",
        item.peer_symbol or "",
        item.fingerprint,
    )


def _sort_exemption(item: Exemption) -> tuple[object, ...]:
    return (item.tool, item.rule, item.path, item.symbol or "", item.id)


def _classify_findings(
    actual: Sequence[Finding],
    by_identity: dict[tuple[object, ...], list[Exemption]],
) -> tuple[
    tuple[Finding, ...],
    tuple[Finding, ...],
    tuple[Finding, ...],
    tuple[Finding, ...],
]:
    """Split actual records into matched, unregistered, duplicate, wildcard."""
    seen: set[tuple[object, ...]] = set()
    matched: list[Finding] = []
    unregistered: list[Finding] = []
    duplicate_findings: list[Finding] = []
    wildcard_findings: list[Finding] = []
    for finding in actual:
        identity = _identity(finding)
        if finding.rule == "*":
            wildcard_findings.append(finding)
        elif identity in seen:
            duplicate_findings.append(finding)
        elif identity not in by_identity:
            unregistered.append(finding)
        else:
            matched.append(finding)
        seen.add(identity)
    return (
        tuple(matched),
        tuple(unregistered),
        tuple(duplicate_findings),
        tuple(wildcard_findings),
    )


def reconcile(
    registry: Registry,
    findings: Iterable[Finding],
    today: date,
) -> AuditResult:
    """Reconcile exact identities against a deny-by-default registry."""
    actual = tuple(sorted(findings, key=_sort_finding))
    registered = tuple(sorted(registry.exemptions, key=_sort_exemption))
    by_identity: dict[tuple[object, ...], list[Exemption]] = {}
    for registered_item in registered:
        by_identity.setdefault(_identity(registered_item), []).append(
            registered_item
        )

    actual_identities = {
        _identity(item) for item in actual if item.rule != "*"
    }
    duplicate_exemptions = tuple(
        duplicate
        for entries in by_identity.values()
        if len(entries) > 1
        for duplicate in entries[1:]
    )
    stale = [
        item for item in registered if _identity(item) not in actual_identities
    ]
    expired = [
        item
        for item in registered
        if item.expires_on is not None and item.expires_on < today
    ]
    matched, unregistered, duplicate_findings, wildcard_findings = (
        _classify_findings(actual, by_identity)
    )
    return AuditResult(
        matched=matched,
        unregistered=unregistered,
        stale=tuple(stale),
        duplicate_findings=duplicate_findings,
        duplicate_exemptions=duplicate_exemptions,
        wildcard_findings=wildcard_findings,
        expired=tuple(expired),
    )


def _tracked_files(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CollectionError(
            f"git file inventory failed: {result.stderr.strip()}"
        )
    return tuple(root / line for line in result.stdout.splitlines() if line)


def _python_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(path for path in paths if path.suffix in {".py", ".pyi"})


def _merge_findings(*groups: Iterable[Finding]) -> tuple[Finding, ...]:
    merged: dict[tuple[object, ...], Finding] = {}
    for group in groups:
        for item in group:
            merged.setdefault(_identity(item), item)
    return tuple(sorted(merged.values(), key=_sort_finding))


def collect_inventory(
    root: Path,
    scope: str,
    python_version: str | None,
) -> tuple[Finding, ...]:
    """Collect the selected repository-wide suppression/diagnostic scope."""
    if scope not in {"full", "cross-file", "source", "policy"}:
        raise CollectionError(f"unknown inventory scope {scope!r}")
    tracked = _tracked_files(root)
    python_paths = _python_paths(tracked)
    if not python_paths:
        raise CollectionError("no tracked Python files for inventory")

    groups: list[Iterable[Finding]] = []
    if scope in {"full", "source", "policy"}:
        groups.extend(
            (
                collect_source_suppressions(root, python_paths),
                collect_secret_markers(root, tracked),
            )
        )
    if scope in {"full", "policy"}:
        groups.extend(
            (
                collect_config_suppressions(root),
                collect_warning_suppressions(root, python_paths),
                collect_ci_suppressions(root),
            )
        )
    if scope in {"full", "cross-file"}:
        groups.append(
            run_cross_file_pylint(
                root,
                tuple(
                    path.relative_to(root).as_posix() for path in python_paths
                ),
                python_version,
            )
        )
    return _merge_findings(*groups)
