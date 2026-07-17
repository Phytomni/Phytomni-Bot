# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Collect secret-scanner allowlist markers across tracked source files."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from ..fingerprints import Endpoint, finding_fingerprint
from ..model import Finding, Mechanism, TargetKind
from .source import collect_source_suppressions

_MARKER_RE = re.compile(
    r"#\s*(?P<marker>nosec(?:\s+(?P<rules>[^\s#]+(?:\s*,\s*[^\s#]+)*))?"
    r"|pragma:\s*allowlist\s+secret)",
    re.IGNORECASE,
)


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _non_python_finding(
    root: Path,
    path: Path,
    line_number: int,
    line: str,
    rules: tuple[str, ...],
) -> tuple[Finding, ...]:
    display_path = _relative_path(root, path)
    output: list[Finding] = []
    for rule in rules:
        symbol = f"line:{line_number}"
        endpoint = Endpoint(display_path, symbol, line.strip())
        output.append(
            Finding(
                tool="secret-scan",
                rule=rule,
                mechanism=Mechanism.MARKER,
                target_kind=TargetKind.FIXTURE,
                path=display_path,
                symbol=symbol,
                peer_path=None,
                peer_symbol=None,
                fingerprint=finding_fingerprint(
                    "secret-scan",
                    rule,
                    Mechanism.MARKER,
                    (endpoint,),
                    line.strip(),
                ),
                location=f"{display_path}:{line_number}",
                message=line.strip(),
                tool_version=None,
            )
        )
    return tuple(output)


def _rules(match: re.Match[str]) -> tuple[str, ...]:
    raw = match.group("rules")
    if raw is None:
        return ("*",)
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or ("*",)


def collect_secret_markers(
    root: Path, paths: Sequence[Path]
) -> tuple[Finding, ...]:
    """Collect Python-token markers and comment markers in other files."""
    findings = list(
        item
        for item in collect_source_suppressions(root, paths)
        if item.tool == "secret-scan"
    )
    for path in sorted(paths, key=lambda item: _relative_path(root, item)):
        if path.suffix in {".py", ".pyi"}:
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in _MARKER_RE.finditer(line):
                findings.extend(
                    _non_python_finding(
                        root, path, line_number, line, _rules(match)
                    )
                )
    return tuple(
        sorted(
            findings, key=lambda item: (item.path, item.location, item.rule)
        )
    )
