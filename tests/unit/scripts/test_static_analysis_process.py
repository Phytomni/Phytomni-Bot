# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for static-analysis process helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from scripts.static_analysis.collectors import process

pytestmark = pytest.mark.unit


def test_tracked_git_files_omits_deleted_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only existing paths from Git's tracked-file inventory are returned."""
    existing = tmp_path / "existing.py"
    existing.write_text("value = 1\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run_command(
        command: Sequence[str], root: Path, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(command), root))
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="existing.py\ndeleted.py\n",
            stderr="",
        )

    monkeypatch.setattr(process, "run_command", fake_run_command)

    assert process.tracked_git_files(tmp_path, ("*.py",)) == (existing,)
    assert calls == [(("git", "ls-files", "*.py"), tmp_path)]
