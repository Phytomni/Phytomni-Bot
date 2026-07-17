# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Ruff suppression reverse probes."""

from __future__ import annotations

from pathlib import Path

from ..model import Finding
from .errors import ReverseEvidence
from .reverse import Runner, json_probe


def audit_ruff(
    root: Path, finding: Finding, runner: Runner | None = None
) -> ReverseEvidence:
    """Run Ruff with ``noqa`` handling disabled for one exact finding."""
    command = (
        "uv",
        "run",
        "ruff",
        "check",
        "--output-format=json",
        "--ignore-noqa",
        finding.path,
    )
    version_command = ("uv", "run", "ruff", "--version")
    return json_probe(root, finding, command, version_command, runner)
