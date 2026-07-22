# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fail-closed process and Git-file helpers shared by collectors."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import CollectionError

Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(
    command: Sequence[str],
    root: Path,
    *,
    runner: Runner | None = None,
    error_message: str,
) -> subprocess.CompletedProcess[str]:
    """Run an injected or real command and normalize launch failures."""
    try:
        execute = subprocess.run if runner is None else runner
        return execute(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise CollectionError(error_message) from exc


def tracked_git_files(
    root: Path,
    patterns: Sequence[str] = (),
) -> tuple[Path, ...]:
    """Return tracked paths from Git, failing closed on command errors."""
    result = run_command(
        ("git", "ls-files", *patterns),
        root,
        error_message="git file inventory executable failed",
    )
    if result.returncode != 0:
        raise CollectionError(
            f"git file inventory failed: {result.stderr.strip()}"
        )
    return tuple(root / line for line in result.stdout.splitlines() if line)
