# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flake8 suppression reverse probes."""

from __future__ import annotations

from pathlib import Path

from ..model import Finding
from .errors import ReverseEvidence
from .reverse import Runner, text_probe


def audit_flake8(
    root: Path, finding: Finding, runner: Runner | None = None
) -> ReverseEvidence:
    """Run Flake8 with ``noqa`` handling disabled for one exact finding."""
    command = (
        "uv",
        "run",
        "flake8",
        "--disable-noqa",
        finding.path,
    )
    version_command = ("uv", "run", "flake8", "--version")
    return text_probe(root, finding, command, version_command, runner)
