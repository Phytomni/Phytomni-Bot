# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared safe metadata and CLI argument helpers for Gauss probes."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

Runner = Callable[..., Any]
_SAFE_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")


def safe_commit(value: str) -> str:
    """Return a bounded commit label or a fixed unknown marker."""
    return value if _SAFE_COMMIT.fullmatch(value) else "unknown"


def resolve_git_commit(
    configured: str,
    *,
    cwd: Path,
    runner: Runner | None = None,
) -> str:
    """Return a safe configured commit or a redacted Git HEAD label."""
    if _SAFE_COMMIT.fullmatch(configured):
        return configured
    execute = subprocess.run if runner is None else runner
    try:
        result = execute(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            cwd=cwd,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    stdout = getattr(result, "stdout", "")
    value = stdout.strip() if isinstance(stdout, str) else ""
    return safe_commit(value)


def add_environment_output_arguments(
    parser: argparse.ArgumentParser,
    *,
    environment_default: str,
    output_default: Path,
) -> None:
    """Add the shared environment and constrained-output CLI arguments."""
    parser.add_argument(
        "--environment-class",
        default=environment_default,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=output_default,
    )
