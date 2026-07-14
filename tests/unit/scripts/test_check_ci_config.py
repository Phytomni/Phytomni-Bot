# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the value-safe CI configuration checker."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_ci_config.py"


def _run(
    *names: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run the checker with a controlled environment."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *names],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_checker_reports_sorted_missing_names_without_values() -> None:
    """Missing-name output never exposes a supplied secret value."""
    env = os.environ.copy()
    env["API_KEY"] = "secret-value-that-must-not-be-printed"
    env.pop("GAUSS_DSN", None)
    env.pop("TOKEN_URL", None)

    result = _run("TOKEN_URL", "GAUSS_DSN", "API_KEY", env=env)

    assert result.returncode == 1
    assert result.stdout == (
        "Missing required CI configuration: GAUSS_DSN, TOKEN_URL\n"
    )
    assert "secret-value-that-must-not-be-printed" not in result.stdout
    assert "secret-value-that-must-not-be-printed" not in result.stderr


def test_checker_reports_count_when_all_names_are_present() -> None:
    """A complete environment exits successfully with a count only."""
    env = os.environ.copy()
    env["TOKEN_URL"] = "https://example.invalid"
    env["GAUSS_DSN"] = "postgresql://user:password@example.invalid/db"

    result = _run("GAUSS_DSN", "TOKEN_URL", env=env)

    assert result.returncode == 0
    assert result.stdout == "Required CI configuration present: 2 names\n"
    assert "postgresql://" not in result.stdout
