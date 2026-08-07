# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Keep internal handoff material out of the tracked documentation tree."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DOCS = tuple(
    ROOT / relative
    for relative in (
        "README.md",
        "docs/README.md",
        "docs/contracts/a2ui/README.md",
        "docs/contracts/deep-genome/README.md",
        "docs/explanation/architecture.md",
        "docs/guides/deployment.md",
        "docs/ops/citation-database-runbook.md",
        "docs/ops/http-api-runbook.md",
        "docs/ops/upgrading.md",
        "docs/reference/cli.md",
        "docs/reference/http-api.md",
    )
)
FORBIDDEN_MARKERS = (
    "docs/handoffs",
    "../handoffs",
    "web-cutover-checklist",
    "historical-gate-audit",
    "owner packet",
)
ACCEPTANCE_ID = re.compile(r"\bRC-[A-Z]+-[0-9]+\b")


def test_internal_document_paths_are_not_public() -> None:
    """Keep packet, cutover, audit, and unfinished-design paths private."""
    assert not (ROOT / "docs/handoffs").exists()
    assert not (ROOT / "docs/ops/web-cutover-checklist.md").exists()
    assert not (ROOT / "docs/ops/historical-gate-audit-2026-07-14.md").exists()
    assert not (ROOT / "docs/design/etl-web-mysql-history.md").exists()


def test_public_docs_do_not_reference_internal_acceptance_material() -> None:
    """Prevent public docs from reintroducing owner-only packet language."""
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            assert marker not in text, f"{marker!r} leaked into {path}"
        assert not ACCEPTANCE_ID.search(text), f"RC-* leaked into {path}"
