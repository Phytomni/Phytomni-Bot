# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Mypy and Pyright suppression reverse probes."""

from __future__ import annotations

from pathlib import Path

from ..model import Finding
from .errors import ReverseEvidence
from .reverse import Runner, json_probe, text_probe


def audit_mypy(
    root: Path, finding: Finding, runner: Runner | None = None
) -> ReverseEvidence:
    """Run Mypy with unused-ignore diagnostics enabled."""
    command = (
        "uv",
        "run",
        "mypy",
        "--warn-unused-ignores",
        "--show-error-codes",
        finding.path,
    )
    version_command = ("uv", "run", "mypy", "--version")
    return text_probe(root, finding, command, version_command, runner)


def audit_pyright(
    root: Path, finding: Finding, runner: Runner | None = None
) -> ReverseEvidence:
    """Run Pyright in JSON mode and require the exact diagnostic code."""
    command = (
        "uv",
        "run",
        "pyright",
        "--outputjson",
        finding.path,
    )
    version_command = ("uv", "run", "pyright", "--version")
    return json_probe(root, finding, command, version_command, runner)
