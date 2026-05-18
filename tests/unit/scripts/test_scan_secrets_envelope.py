# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the scan_secrets .env.encrypted envelope exemption.

A real PHYBOT01 envelope must pass every scan mode (path-name,
worktree, staged), while a plaintext file misnamed .env.encrypted
must still be blocked, and a normal plaintext .env stays flagged.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.config import encrypt_env_file

pytestmark = pytest.mark.unit

LICENSE = "customer-license-key-001"


def test_sensitive_path_reason_allowlists_envelope_name(
    secret_scanner,
) -> None:
    """Verify .env.encrypted is allowlisted but plaintext .env is not.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    assert secret_scanner.sensitive_path_reason(".env.encrypted") is None
    assert (
        secret_scanner.sensitive_path_reason(
            "src/mcp_server_phytomni/config/.env"
        )
        is not None
    )


def test_envelope_finding_passes_real_envelope(
    secret_scanner, tmp_path
) -> None:
    """Verify a real PHYBOT01 blob produces no finding.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
        tmp_path: Temporary directory fixture for file I/O.
    """
    src = tmp_path / ".env"
    src.write_text("API_KEY=sk-123\n", encoding="utf-8")
    blob_path = tmp_path / ".env.encrypted"
    encrypt_env_file(src, LICENSE, blob_path)

    finding = secret_scanner.envelope_path_finding(
        "tracked", ".env.encrypted", blob_path.read_bytes()
    )

    assert finding is None


def test_envelope_finding_blocks_misnamed_plaintext(secret_scanner) -> None:
    """Verify a plaintext file misnamed .env.encrypted is flagged.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    finding = secret_scanner.envelope_path_finding(
        "staged", "config/.env.encrypted", b"API_KEY=sk-real-secret\n"
    )

    assert finding is not None
    assert finding.rule == "sensitive-path"
    assert "PHYBOT01" in finding.message


def test_envelope_finding_ignores_non_envelope_names(secret_scanner) -> None:
    """Verify non-envelope paths are a no-op for the helper.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    assert (
        secret_scanner.envelope_path_finding("tracked", "notes.txt", b"hello")
        is None
    )


def test_scan_worktree_path_exempts_real_envelope(
    secret_scanner, tmp_path, monkeypatch
) -> None:
    """Verify worktree scan exempts a real envelope, blocks plaintext.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
        tmp_path: Temporary directory fixture for file I/O.
        monkeypatch: Pytest monkeypatch fixture to switch cwd.
    """
    scanner = secret_scanner
    src = tmp_path / ".env"
    src.write_text("API_KEY=sk-123\n", encoding="utf-8")
    good = tmp_path / "good"
    good.mkdir()
    encrypt_env_file(src, LICENSE, good / ".env.encrypted")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ".env.encrypted").write_text(
        "API_KEY=sk-leaked\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert scanner.scan_worktree_path("good/.env.encrypted") == []
    flagged = scanner.scan_worktree_path("bad/.env.encrypted")
    assert flagged and flagged[0].rule == "sensitive-path"
    assert "PHYBOT01" in flagged[0].message
