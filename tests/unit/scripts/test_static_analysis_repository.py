# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Repository-level registry reconciliation tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from scripts.static_analysis import inventory as inventory_module
from scripts.static_analysis.inventory import collect_inventory, reconcile
from scripts.static_analysis.model import (
    Finding,
    Mechanism,
    TargetKind,
    load_registry,
)

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]


def test_inventory_merge_does_not_drop_duplicate_findings() -> None:
    """Duplicate identities remain visible for reconciliation to reject."""
    finding = Finding(
        tool="pylint",
        rule="R0801",
        mechanism=Mechanism.DIAGNOSTIC,
        target_kind=TargetKind.PAIR,
        path="tests/one.py",
        symbol="1:2",
        peer_path="tests/two.py",
        peer_symbol="3:4",
        fingerprint="sha256:" + "0" * 64,
        location="tests/one.py:1",
        message="duplicate",
        tool_version="pylint 4.0.6",
    )

    merge_findings = getattr(inventory_module, "_merge_findings")

    assert merge_findings((finding, finding)) == (finding, finding)


def test_repository_inventory_matches_registry() -> None:
    """Every tracked finding must have one exact registry authorization."""
    registry = load_registry(
        _ROOT / "static-analysis-exemptions.toml",
        today=date(2026, 7, 17),
    )
    findings = collect_inventory(_ROOT, "full", None)

    assert reconcile(registry, findings, date(2026, 7, 17)).is_clean
