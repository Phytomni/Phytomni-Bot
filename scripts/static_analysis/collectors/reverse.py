# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fail-closed subprocess helpers for suppression reverse probes."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..model import Finding
from .errors import CollectionError, ReverseEvidence

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    command: Sequence[str], root: Path, runner: Runner | None
) -> subprocess.CompletedProcess[str]:
    try:
        if runner is not None:
            return runner(
                list(command),
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        return subprocess.run(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise CollectionError(
            f"reverse probe executable failed: {' '.join(command)}"
        ) from exc


def _version(root: Path, command: Sequence[str], runner: Runner | None) -> str:
    result = _run(command, root, runner)
    if result.returncode != 0:
        raise CollectionError(
            f"version command failed ({result.returncode}): "
            f"{' '.join(command)}"
        )
    output = (result.stdout or result.stderr).strip()
    if not output:
        raise CollectionError("version command returned empty output")
    return output.splitlines()[0]


def _status_output(
    result: subprocess.CompletedProcess[str], command: Sequence[str]
) -> str:
    if result.returncode not in {0, 1}:
        raise CollectionError(
            f"reverse probe returned unexpected status {result.returncode}: "
            f"{' '.join(command)}"
        )
    output = (result.stdout or "").strip()
    if not output:
        output = (result.stderr or "").strip()
    if not output:
        raise CollectionError("reverse probe returned empty output")
    return output


def text_probe(
    root: Path,
    finding: Finding,
    command: Sequence[str],
    version_command: Sequence[str],
    runner: Runner | None = None,
) -> ReverseEvidence:
    """Run a text-output tool and require the exact rule to remain present."""
    version = _version(root, version_command, runner)
    result = _run(command, root, runner)
    output = _status_output(result, command)
    if finding.rule not in output:
        raise CollectionError(
            f"stale suppression: {finding.tool}:{finding.rule}:{finding.path}"
        )
    return ReverseEvidence(
        finding=finding,
        command=tuple(command),
        returncode=result.returncode,
        output=f"{version}\n{output}",
        necessary=True,
    )


def json_probe(
    root: Path,
    finding: Finding,
    command: Sequence[str],
    version_command: Sequence[str],
    runner: Runner | None = None,
) -> ReverseEvidence:
    """Run a JSON-output tool and require a matching diagnostic object."""
    version = _version(root, version_command, runner)
    result = _run(command, root, runner)
    output = _status_output(result, command)
    try:
        document: Any = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CollectionError("reverse probe returned malformed JSON") from exc
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict):
        records = document.get("generalDiagnostics", [])
    else:
        raise CollectionError("reverse probe JSON must be an array/object")
    if not isinstance(records, list) or not any(
        isinstance(record, dict)
        and (
            record.get("code") == finding.rule
            or record.get("rule") == finding.rule
            or record.get("rule_id") == finding.rule
            or record.get("ruleId") == finding.rule
        )
        for record in records
    ):
        raise CollectionError(
            f"stale suppression: {finding.tool}:{finding.rule}:{finding.path}"
        )
    return ReverseEvidence(
        finding=finding,
        command=tuple(command),
        returncode=result.returncode,
        output=f"{version}\n{output}",
        necessary=True,
    )
