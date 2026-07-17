# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the exact static-analysis exemption registry model."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from scripts.static_analysis.model import (
    Classification,
    Mechanism,
    RegistryError,
    TargetKind,
    load_registry,
)

pytestmark = pytest.mark.unit


FINGERPRINT = "sha256:" + "a" * 64


def _entry(**overrides: object) -> str:
    values: dict[str, object] = {
        "id": "SAE-TMP-0001",
        "tool": "pylint",
        "rule": "R0903",
        "classification": "temporary",
        "mechanism": "diagnostic",
        "target_kind": "symbol",
        "path": "src/example.py",
        "symbol": "Example",
        "fingerprint": FINGERPRINT,
        "owner": "bot-maintainers",
        "introduced_on": date(2026, 7, 17),
        "review_on": date(2026, 7, 31),
        "expires_on": date(2026, 8, 31),
        "remediation": "SAE-WORK-0001",
        "rationale": "Existing debt needs a bounded refactor.",
        "counterfactual": "A direct removal changes the tested boundary.",
        "risk": "The class may remain underspecified.",
        "tests": ["tests/unit/test_example.py"],
    }
    values.update(overrides)
    lines: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, date):
            rendered = value.isoformat()
        elif isinstance(value, list):
            rendered = "[" + ", ".join(f'"{item}"' for item in value) + "]"
        else:
            rendered = f'"{value}"'
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines)


def _write_registry(tmp_path: Path, *entries: str, **policy: object) -> Path:
    registry = tmp_path / "exceptions.toml"
    default = policy.get("default", "deny")
    registry.write_text(
        "schema_version = 1\n"
        f'[policy]\ndefault = "{default}"\n'
        + "\n".join(f"[[exemptions]]\n{entry}" for entry in entries)
        + "\n",
        encoding="utf-8",
    )
    return registry


def test_temporary_entry_requires_expiry_and_remediation(
    tmp_path: Path,
) -> None:
    """Temporary entries cannot be loaded without a bounded migration."""
    entry = _entry(expires_on=None, remediation=None)
    # The fields are omitted, rather than serialized as TOML nulls.
    entry = "\n".join(
        line
        for line in entry.splitlines()
        if not line.startswith(("expires_on =", "remediation ="))
    )
    registry = _write_registry(tmp_path, entry)

    with pytest.raises(RegistryError, match="expires_on"):
        load_registry(registry, today=date(2026, 7, 17))


def test_structural_symbol_entry_loads_as_typed_values(tmp_path: Path) -> None:
    """A valid structural symbol retains exact enum and date values."""
    entry = _entry(
        classification="structural",
        expires_on=None,
        remediation=None,
    )
    entry = "\n".join(
        line
        for line in entry.splitlines()
        if not line.startswith(("expires_on =", "remediation ="))
    )
    registry = load_registry(
        _write_registry(tmp_path, entry), today=date(2026, 7, 17)
    )

    assert registry.schema_version == 1
    assert registry.default == "deny"
    assert len(registry.exemptions) == 1
    exemption = registry.exemptions[0]
    assert exemption.classification is Classification.STRUCTURAL
    assert exemption.mechanism is Mechanism.DIAGNOSTIC
    assert exemption.target_kind is TargetKind.SYMBOL
    assert exemption.expires_on is None
    assert exemption.remediation is None


def test_pair_target_requires_two_exact_endpoints(tmp_path: Path) -> None:
    """Pair authorizations carry both endpoints and reject incomplete pairs."""
    entry = _entry(
        target_kind="pair",
        symbol="A.run",
        peer_path="src/other.py",
        peer_symbol="B.run",
    )
    loaded = load_registry(
        _write_registry(tmp_path, entry), today=date(2026, 7, 17)
    )
    exemption = loaded.exemptions[0]
    assert exemption.target_kind is TargetKind.PAIR
    assert exemption.peer_path == "src/other.py"
    assert exemption.peer_symbol == "B.run"

    broken = _entry(target_kind="pair", symbol="A.run")
    with pytest.raises(RegistryError, match="peer_path"):
        load_registry(
            _write_registry(tmp_path, broken), today=date(2026, 7, 17)
        )


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ((_entry(), _entry()), "duplicate id"),
        (
            (
                _entry(
                    introduced_on=date(2026, 7, 1),
                    expires_on=date(2026, 7, 16),
                ),
            ),
            "expired",
        ),
        ((_entry(fingerprint="sha256:ABC"),), "fingerprint"),
        ((_entry(symbol=None),), "symbol"),
    ],
)
def test_invalid_registry_entries_fail_closed(
    tmp_path: Path, entries: tuple[str, ...], message: str
) -> None:
    """Duplicate, expired, malformed, and incomplete entries are rejected."""
    with pytest.raises(RegistryError, match=message):
        load_registry(
            _write_registry(tmp_path, *entries), today=date(2026, 7, 17)
        )


def test_unknown_keys_and_policy_values_fail_closed(tmp_path: Path) -> None:
    """Schema drift cannot silently broaden the registry."""
    unknown_entry = _entry() + '\nfuture_key = "ignored?"'
    with pytest.raises(RegistryError, match="unknown key"):
        load_registry(_write_registry(tmp_path, unknown_entry))

    with pytest.raises(RegistryError, match="default"):
        load_registry(_write_registry(tmp_path, _entry(), default="allow"))

    invalid_top_level = tmp_path / "invalid-top-level.toml"
    invalid_top_level.write_text(
        "schema_version = 1\nunexpected = true\n"
        '[policy]\ndefault = "deny"\n',
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="top-level"):
        load_registry(invalid_top_level)
