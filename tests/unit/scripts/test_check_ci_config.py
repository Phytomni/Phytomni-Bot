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

from mcp_server_phytomni.config.required_env import (
    REQUIRED_DEPLOYMENT_FIELDS,
    REQUIRED_OPERATOR_SECRET_FIELDS,
)

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_ci_config.py"


def test_lint_workflow_uses_gauss_test_dsn_without_retired_bi_token() -> None:
    """Offline lint jobs use a test DSN instead of the retired BI token."""
    text = Path(".github/workflows/lint.yml").read_text(encoding="utf-8")
    assert "BI_TOKEN" not in text
    assert text.count("GAUSS_DSN: postgresql://pytest") == 2
    assert 'PHYTOMNI_TESTING: "1"' in text


def test_nightly_declares_current_runtime_inputs() -> None:
    """Nightly CI declares every current runtime input by source name."""
    text = Path(".github/workflows/e2e-nightly.yml").read_text(
        encoding="utf-8"
    )
    assert "BI_TOKEN" not in text
    assert "GAUSS_DSN: ${{ secrets.GAUSS_DSN }}" in text
    for name in REQUIRED_DEPLOYMENT_FIELDS:
        assert f"{name}: ${{{{ vars.{name} }}}}" in text
    for name in REQUIRED_OPERATOR_SECRET_FIELDS:
        assert f"{name}: ${{{{ secrets.{name} }}}}" in text
    assert "python scripts/check_ci_config.py" in text


def test_nightly_remains_manual_and_scheduled_only() -> None:
    """Nightly CI cannot be activated by pushes or pull requests."""
    text = Path(".github/workflows/e2e-nightly.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "pull_request:" not in text
    assert "push:" not in text


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
