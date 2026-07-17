# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for exact finding-to-registry reconciliation."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from scripts.static_analysis.inventory import reconcile
from scripts.static_analysis.model import (
    Classification,
    Exemption,
    Finding,
    Mechanism,
    Registry,
    TargetKind,
)

pytestmark = pytest.mark.unit


def make_finding(**overrides: Any) -> Finding:
    """Build a complete deterministic finding for one test case."""
    base = Finding(
        tool="ruff",
        rule="E501",
        mechanism=Mechanism.INLINE,
        target_kind=TargetKind.SYMBOL,
        path="src/example.py",
        symbol="sample",
        peer_path=None,
        peer_symbol=None,
        fingerprint="sha256:" + "1" * 64,
        location="src/example.py:1",
        message="line too long",
        tool_version="ruff 0.12.0",
    )
    return replace(base, **overrides)


def make_exemption(**overrides: Any) -> Exemption:
    """Build a complete deterministic registry entry for one test case."""
    base = Exemption(
        id="SAE-TMP-0001",
        tool="ruff",
        rule="E501",
        classification=Classification.TEMPORARY,
        mechanism=Mechanism.INLINE,
        target_kind=TargetKind.SYMBOL,
        path="src/example.py",
        symbol="sample",
        peer_path=None,
        peer_symbol=None,
        fingerprint="sha256:" + "0" * 64,
        owner="bot-maintainers",
        introduced_on=date(2026, 7, 17),
        review_on=date(2026, 8, 15),
        rationale="fixture",
        counterfactual="fixture",
        risk="fixture",
        tests=("fixture",),
        expires_on=date(2026, 8, 31),
        remediation="SAE-WORK-INITIAL-AUDIT",
    )
    return replace(base, **overrides)


def make_registry(*exemptions: Exemption) -> Registry:
    """Build a deny-by-default registry with explicit entries."""
    return Registry(schema_version=1, default="deny", exemptions=exemptions)


def test_reconcile_rejects_replacement_at_same_count() -> None:
    """A changed fingerprint is both unregistered and stale."""
    registered = make_exemption(fingerprint="sha256:" + "0" * 64)
    actual = make_finding(fingerprint="sha256:" + "1" * 64)
    result = reconcile(make_registry(registered), [actual], date(2026, 7, 17))

    assert result.unregistered == (actual,)
    assert result.stale == (registered,)
    assert not result.is_clean


def test_reconcile_accepts_exact_identity() -> None:
    """All identity fields must match before a finding is authorized."""
    finding = make_finding()
    entry = make_exemption(
        tool=finding.tool,
        rule=finding.rule,
        mechanism=finding.mechanism,
        target_kind=finding.target_kind,
        path=finding.path,
        symbol=finding.symbol,
        fingerprint=finding.fingerprint,
    )
    result = reconcile(make_registry(entry), [finding], date(2026, 7, 17))

    assert result.is_clean
    assert result.matched == (finding,)


def test_reconcile_rejects_duplicate_and_wildcard_findings() -> None:
    """Duplicate actual records and bare wildcard directives fail closed."""
    finding = make_finding()
    wildcard = make_finding(rule="*")
    entry = make_exemption(
        tool=finding.tool,
        rule=finding.rule,
        mechanism=finding.mechanism,
        target_kind=finding.target_kind,
        path=finding.path,
        symbol=finding.symbol,
        fingerprint=finding.fingerprint,
    )
    result = reconcile(
        make_registry(entry),
        [finding, finding, wildcard],
        date(2026, 7, 17),
    )

    assert result.duplicate_findings == (finding,)
    assert result.wildcard_findings == (wildcard,)
    assert not result.is_clean


def test_reconcile_rejects_duplicate_registry_authorization() -> None:
    """Two IDs cannot authorize one exact identity."""
    finding = make_finding()
    first = make_exemption(
        id="SAE-TMP-0001",
        tool=finding.tool,
        rule=finding.rule,
        mechanism=finding.mechanism,
        target_kind=finding.target_kind,
        path=finding.path,
        symbol=finding.symbol,
        fingerprint=finding.fingerprint,
    )
    second = replace(first, id="SAE-TMP-0002")
    result = reconcile(
        make_registry(first, second), [finding], date(2026, 7, 17)
    )

    assert result.duplicate_exemptions == (second,)
    assert not result.is_clean


def test_reconcile_reports_expired_entries() -> None:
    """Expiry remains a policy mismatch even when the finding still exists."""
    finding = make_finding()
    entry = make_exemption(
        tool=finding.tool,
        rule=finding.rule,
        mechanism=finding.mechanism,
        target_kind=finding.target_kind,
        path=finding.path,
        symbol=finding.symbol,
        fingerprint=finding.fingerprint,
        expires_on=date(2026, 7, 16),
    )
    result = reconcile(make_registry(entry), [finding], date(2026, 7, 17))

    assert result.expired == (entry,)
    assert not result.is_clean


def test_reconcile_sorting_is_stable() -> None:
    """Input order cannot change the diagnostic order in reports."""
    first = make_finding(path="b.py")
    second = make_finding(path="a.py")
    result = reconcile(make_registry(), [first, second], date(2026, 7, 17))

    assert result.unregistered == (second, first)
