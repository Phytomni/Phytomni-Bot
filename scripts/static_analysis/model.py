# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data model and fail-closed loader for static-analysis exemptions."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict


class RegistryError(ValueError):
    """Raised when the exemption registry is malformed or unsafe."""


class _ValueEnum(StrEnum):
    """String enum with TOML-friendly values."""


class Classification(_ValueEnum):
    """The approved lifecycle classification for an exemption."""

    STRUCTURAL = "structural"
    TEMPORARY = "temporary"


class TargetKind(_ValueEnum):
    """The exact target identity represented by an exemption."""

    SYMBOL = "symbol"
    SPAN = "span"
    PAIR = "pair"
    CONFIG = "config"
    COMMAND = "command"
    FIXTURE = "fixture"


class Mechanism(_ValueEnum):
    """The static-analysis mechanism that creates a finding."""

    DIAGNOSTIC = "diagnostic"
    INLINE = "inline"
    CONFIG = "config"
    COMMAND = "command"
    DECORATOR = "decorator"
    MARKER = "marker"


class FindingParts(TypedDict):
    """Normalized fields shared by every collector finding builder."""

    tool: str
    rule: str
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
    location: str
    message: str
    source: str


@dataclass(frozen=True, slots=True)
class Finding:  # pylint: disable=too-many-instance-attributes
    """One observed static-analysis exception or diagnostic."""

    tool: str
    rule: str
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
    fingerprint: str
    location: str
    message: str
    tool_version: str | None


@dataclass(frozen=True, slots=True)
class Exemption:  # pylint: disable=too-many-instance-attributes
    """One exact, reviewed authorization in the registry."""

    id: str
    tool: str
    rule: str
    classification: Classification
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
    fingerprint: str
    owner: str
    introduced_on: date
    review_on: date
    rationale: str
    counterfactual: str
    risk: str
    tests: tuple[str, ...]
    expires_on: date | None
    remediation: str | None


@dataclass(frozen=True, slots=True)
class Registry:
    """The deny-by-default registry and its exact exemptions."""

    schema_version: int
    default: str
    exemptions: tuple[Exemption, ...]


_TOP_LEVEL_KEYS = frozenset({"schema_version", "policy", "exemptions"})
_POLICY_KEYS = frozenset({"default"})
_ENTRY_KEYS = frozenset(
    {
        "id",
        "tool",
        "rule",
        "classification",
        "mechanism",
        "target_kind",
        "path",
        "symbol",
        "peer_path",
        "peer_symbol",
        "fingerprint",
        "owner",
        "introduced_on",
        "review_on",
        "expires_on",
        "remediation",
        "rationale",
        "counterfactual",
        "risk",
        "tests",
    }
)
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_PATH_RE = re.compile(r"^[^\x00]+$")
_WILDCARD_PATHS = frozenset({"typings/**/*.pyi"})


class _IdentityData(TypedDict):
    """Parsed immutable target fields before model construction."""

    entry_id: str
    tool: str
    rule: str
    classification: Classification
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
    fingerprint: str


class _ReviewData(TypedDict):
    """Parsed review and temporary-lifecycle fields."""

    owner: str
    introduced_on: date
    review_on: date
    rationale: str
    counterfactual: str
    risk: str
    tests: tuple[str, ...]
    expires_on: date | None
    remediation: str | None


def _error(message: str) -> RegistryError:
    """Build a consistently worded schema error."""
    return RegistryError(f"registry: {message}")


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{key} must be a non-empty string")
    return value


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{key} must be a non-empty string when provided")
    return value


def _enum_value(
    enum_type: type[_ValueEnum], raw: dict[str, Any], key: str
) -> Any:
    value = _required_string(raw, key)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _error(f"unknown {key} {value!r}") from exc


def _date_value(
    raw: dict[str, Any], key: str, *, required: bool
) -> date | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise _error(f"{key} is required")
        return None
    if not isinstance(value, date):
        raise _error(f"{key} must be a TOML date")
    return value


def _validate_path(path: str) -> None:
    if not _SAFE_PATH_RE.match(path) or path.startswith("/"):
        raise _error(f"path must be a relative non-empty path: {path!r}")
    if ".." in Path(path).parts:
        raise _error(f"path may not escape the repository: {path!r}")
    if any(char in path for char in "*?[") and path not in _WILDCARD_PATHS:
        raise _error(f"unbounded path pattern is not allowed: {path!r}")


def _validate_target(
    target_kind: TargetKind,
    path: str,
    symbol: str | None,
    peer_path: str | None,
    peer_symbol: str | None,
) -> None:
    _validate_path(path)
    if target_kind is TargetKind.SYMBOL and symbol is None:
        raise _error("symbol target requires symbol")
    if target_kind is TargetKind.PAIR:
        if peer_path is None:
            raise _error("pair target requires peer_path")
        if peer_symbol is None:
            raise _error("pair target requires peer_symbol")
        _validate_path(peer_path)
    elif peer_path is not None or peer_symbol is not None:
        raise _error(
            "peer_path and peer_symbol are only valid for pair targets"
        )


def _parse_identity(raw: dict[str, Any], ids: set[str]) -> _IdentityData:
    """Parse the immutable finding identity and reject duplicate IDs."""
    entry_id = _required_string(raw, "id")
    if entry_id in ids:
        raise _error(f"duplicate id {entry_id!r}")
    ids.add(entry_id)

    tool = _required_string(raw, "tool")
    rule = _required_string(raw, "rule")
    if rule == "*":
        raise _error("rule may not be a wildcard")
    classification = _enum_value(Classification, raw, "classification")
    mechanism = _enum_value(Mechanism, raw, "mechanism")
    target_kind = _enum_value(TargetKind, raw, "target_kind")
    path = _required_string(raw, "path")
    symbol = _optional_string(raw, "symbol")
    peer_path = _optional_string(raw, "peer_path")
    peer_symbol = _optional_string(raw, "peer_symbol")
    _validate_target(target_kind, path, symbol, peer_path, peer_symbol)

    fingerprint = _required_string(raw, "fingerprint")
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise _error(
            "fingerprint must match sha256:<64 lowercase hex>: "
            f"{fingerprint!r}"
        )
    return {
        "entry_id": entry_id,
        "tool": tool,
        "rule": rule,
        "classification": classification,
        "mechanism": mechanism,
        "target_kind": target_kind,
        "path": path,
        "symbol": symbol,
        "peer_path": peer_path,
        "peer_symbol": peer_symbol,
        "fingerprint": fingerprint,
    }


def _parse_review(
    raw: dict[str, Any], *, today: date, entry_id: str
) -> _ReviewData:
    """Parse review metadata and enforce temporary lifecycle rules."""
    owner = _required_string(raw, "owner")
    introduced_on = _date_value(raw, "introduced_on", required=True)
    review_on = _date_value(raw, "review_on", required=True)
    assert introduced_on is not None
    assert review_on is not None
    if review_on < introduced_on:
        raise _error("review_on must not precede introduced_on")

    rationale = _required_string(raw, "rationale")
    counterfactual = _required_string(raw, "counterfactual")
    risk = _required_string(raw, "risk")
    tests_raw = raw.get("tests")
    if not isinstance(tests_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in tests_raw
    ):
        raise _error("tests must be a list of non-empty strings")
    tests = tuple(tests_raw)

    expires_on = _date_value(raw, "expires_on", required=False)
    remediation = _optional_string(raw, "remediation")
    classification = _enum_value(Classification, raw, "classification")
    if classification is Classification.TEMPORARY:
        if expires_on is None:
            raise _error("temporary entry requires expires_on")
        if remediation is None:
            raise _error("temporary entry requires remediation")
        if expires_on < introduced_on:
            raise _error("expires_on must not precede introduced_on")
        if expires_on < today:
            raise _error(f"temporary entry {entry_id!r} is expired")
    elif expires_on is not None or remediation is not None:
        raise _error(
            "structural entry may not define temporary lifecycle fields"
        )
    return {
        "owner": owner,
        "introduced_on": introduced_on,
        "review_on": review_on,
        "rationale": rationale,
        "counterfactual": counterfactual,
        "risk": risk,
        "tests": tests,
        "expires_on": expires_on,
        "remediation": remediation,
    }


def _validate_entry(raw: object, *, today: date, ids: set[str]) -> Exemption:
    if not isinstance(raw, dict):
        raise _error("each exemption must be a table")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise _error(
            f"unknown key(s) in exemption: {', '.join(sorted(unknown))}"
        )

    identity = _parse_identity(raw, ids)
    review = _parse_review(raw, today=today, entry_id=identity["entry_id"])

    return Exemption(
        id=identity["entry_id"],
        tool=identity["tool"],
        rule=identity["rule"],
        classification=identity["classification"],
        mechanism=identity["mechanism"],
        target_kind=identity["target_kind"],
        path=identity["path"],
        symbol=identity["symbol"],
        peer_path=identity["peer_path"],
        peer_symbol=identity["peer_symbol"],
        fingerprint=identity["fingerprint"],
        owner=review["owner"],
        introduced_on=review["introduced_on"],
        review_on=review["review_on"],
        rationale=review["rationale"],
        counterfactual=review["counterfactual"],
        risk=review["risk"],
        tests=review["tests"],
        expires_on=review["expires_on"],
        remediation=review["remediation"],
    )


def load_registry(path: Path, today: date | None = None) -> Registry:
    """Load and validate a deny-by-default TOML registry.

    Args:
        path: Registry path.
        today: Date used for temporary-entry expiry checks. Defaults to the
            local current date and is injectable for deterministic tests.

    Raises:
        RegistryError: If the document is malformed or violates the schema.
        FileNotFoundError: If ``path`` does not exist.
    """
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise _error(f"invalid TOML: {exc}") from exc

    if not isinstance(document, dict):
        raise _error("top-level document must be a table")
    unknown = set(document) - _TOP_LEVEL_KEYS
    if unknown:
        raise _error(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    schema_version = document.get("schema_version")
    if schema_version != 1 or isinstance(schema_version, bool):
        raise _error("schema_version must be 1")

    policy = document.get("policy")
    if not isinstance(policy, dict):
        raise _error("policy must be a table")
    unknown_policy = set(policy) - _POLICY_KEYS
    if unknown_policy:
        raise _error(
            f"unknown policy key(s): {', '.join(sorted(unknown_policy))}"
        )
    default = policy.get("default")
    if default != "deny":
        raise _error("policy default must be deny")

    raw_exemptions = document.get("exemptions", [])
    if not isinstance(raw_exemptions, list):
        raise _error("exemptions must be an array of tables")
    effective_today = today or date.today()
    ids: set[str] = set()
    exemptions = tuple(
        _validate_entry(item, today=effective_today, ids=ids)
        for item in raw_exemptions
    )
    return Registry(
        schema_version=schema_version,
        default=default,
        exemptions=exemptions,
    )
