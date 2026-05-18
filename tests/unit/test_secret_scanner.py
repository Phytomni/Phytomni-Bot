# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the repository secret scanner.

Covers loading the scanner script, detecting token assignments, allowing
placeholder values, and reporting tracked sensitive paths.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_scan_text_detects_secret_assignment(secret_scanner) -> None:
    """Secret-looking values are reported.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    secret_value = "sk-" + ("A" * 32)

    findings = secret_scanner.scan_text(
        "test",
        "example.py",
        f'OPENAI_API_KEY = "{secret_value}"',
    )

    assert findings
    assert findings[0].rule == "openai-token"


def test_scan_text_allows_placeholders(secret_scanner) -> None:
    """Placeholder values in example files are allowed.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    findings = secret_scanner.scan_text(
        "test",
        "src/mcp_server_phytomni/config/.env.example",
        "OPENAI_API_KEY=your-openai-api-key",
    )

    assert findings == []


def test_scan_text_detects_sensitive_paths(secret_scanner) -> None:
    """Tracked environment files are reported by path.

    Args:
        secret_scanner: Loaded scan_secrets module fixture.
    """
    findings = secret_scanner.scan_text(
        "test", "src/mcp_server_phytomni/.env", ""
    )

    assert findings
    assert findings[0].rule == "sensitive-path"
