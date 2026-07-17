# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the generated static-analysis exception ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.static_analysis.report import render_repository_markdown

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]


def test_lint_ledger_is_generated_from_registry() -> None:
    """The tracked ledger must be byte-identical to its renderer output."""
    expected = render_repository_markdown(_ROOT)
    actual = (_ROOT / "docs/development/lint-exemptions.md").read_text(
        encoding="utf-8"
    )

    assert actual == expected


def test_lint_ledger_contains_lifecycle_and_evidence_fields() -> None:
    """Generated prose exposes the fields needed for human review."""
    rendered = render_repository_markdown(_ROOT)

    assert "Regeneration" in rendered
    assert "Fingerprint" in rendered
    assert "Owner" in rendered
    assert "Review" in rendered
    assert "Expiry" in rendered
    assert "Remediation" in rendered
    assert "Tests" in rendered
    assert "numeric baseline" not in rendered.lower()
