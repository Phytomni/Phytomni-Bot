# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the repository secret scanner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_secret_scanner() -> ModuleType:
    """Load the scanner script as a module."""
    script_path = Path(__file__).parents[2] / "scripts" / "scan_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_secrets", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scan_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module


def test_scan_text_detects_secret_assignment() -> None:
    """Secret-looking values are reported."""
    scanner = load_secret_scanner()
    secret_value = "sk-" + ("A" * 32)

    findings = scanner.scan_text(
        "test",
        "example.py",
        f'OPENAI_API_KEY = "{secret_value}"',
    )

    assert findings
    assert findings[0].rule == "openai-token"


def test_scan_text_allows_placeholders() -> None:
    """Placeholder values in example files are allowed."""
    scanner = load_secret_scanner()

    findings = scanner.scan_text(
        "test",
        "src/mcp_server_phytomni/config/.env.example",
        "OPENAI_API_KEY=your-openai-api-key",
    )

    assert findings == []


def test_scan_text_detects_sensitive_paths() -> None:
    """Tracked environment files are reported by path."""
    scanner = load_secret_scanner()

    findings = scanner.scan_text("test", "src/mcp_server_phytomni/.env", "")

    assert findings
    assert findings[0].rule == "sensitive-path"
