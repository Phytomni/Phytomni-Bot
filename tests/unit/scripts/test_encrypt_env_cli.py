# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the scripts/encrypt_env.py operator CLI.

Drives the script through a subprocess (as an operator would) and
asserts the produced blob round-trips, plus the input-missing,
overwrite-guard, and non-UTF-8 input exit codes.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server_phytomni.config import decrypt_env_blob

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "encrypt_env.py"
LICENSE = "customer-license-key-001"


def _run(*args):
    """Run encrypt_env.py with the test interpreter.

    Args:
        *args: Extra command-line arguments after the script path.

    Returns:
        The completed process with captured text output.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_round_trip(tmp_path):
    """Verify the CLI output decrypts back to the original mapping.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_text("API_KEY=sk-123\nBI_TOKEN=tok\n", encoding="utf-8")
    out = tmp_path / ".env.encrypted"

    result = _run(
        "--input",
        str(src),
        "--license-key",
        LICENSE,
        "--output",
        str(out),
    )

    assert result.returncode == 0, result.stderr
    assert "sha256=" in result.stdout
    assert decrypt_env_blob(out.read_bytes(), LICENSE) == {
        "API_KEY": "sk-123",
        "BI_TOKEN": "tok",
    }


def test_cli_missing_input_exits_2(tmp_path):
    """Verify a missing input path exits with code 2.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    result = _run(
        "--input",
        str(tmp_path / "nope.env"),
        "--license-key",
        LICENSE,
        "--output",
        str(tmp_path / "out.encrypted"),
    )

    assert result.returncode == 2
    assert "input not found" in result.stderr


def test_cli_refuses_overwrite_without_force(tmp_path):
    """Verify an existing output without --force exits with code 3.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_text("API_KEY=sk-123\n", encoding="utf-8")
    out = tmp_path / ".env.encrypted"
    out.write_bytes(b"existing")

    result = _run(
        "--input",
        str(src),
        "--license-key",
        LICENSE,
        "--output",
        str(out),
    )

    assert result.returncode == 3
    assert "output exists" in result.stderr


def test_cli_force_overwrites(tmp_path):
    """Verify --force overwrites an existing output and succeeds.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_text("API_KEY=sk-xyz\n", encoding="utf-8")
    out = tmp_path / ".env.encrypted"
    out.write_bytes(b"stale")

    result = _run(
        "--input",
        str(src),
        "--license-key",
        LICENSE,
        "--output",
        str(out),
        "--force",
    )

    assert result.returncode == 0, result.stderr
    assert decrypt_env_blob(out.read_bytes(), LICENSE) == {
        "API_KEY": "sk-xyz",
    }


def test_cli_rejects_non_utf8_input(tmp_path):
    """Verify a non-UTF-8 input exits with code 4 and a clear message.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_bytes("BASE_URL=请\n".encode("gbk"))
    out = tmp_path / ".env.encrypted"

    result = _run(
        "--input",
        str(src),
        "--license-key",
        LICENSE,
        "--output",
        str(out),
    )

    assert result.returncode == 4
    assert "not UTF-8" in result.stderr
    assert not out.exists()


def test_cli_rejects_bom_input(tmp_path):
    """Verify a UTF-8 BOM input exits with code 4 and a clear message.

    Old Windows editors save "UTF-8" as UTF-8-with-BOM, which decodes
    cleanly but corrupts the first env key. The CLI must reject it at
    build time with the same exit code 4 as a non-UTF-8 input.

    Args:
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_bytes(b"\xef\xbb\xbfBASE_URL=x\n")
    out = tmp_path / ".env.encrypted"

    result = _run(
        "--input",
        str(src),
        "--license-key",
        LICENSE,
        "--output",
        str(out),
    )

    assert result.returncode == 4
    assert "BOM" in result.stderr
    assert not out.exists()
