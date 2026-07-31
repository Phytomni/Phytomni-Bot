# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared Review marker contracts and bounded identity helpers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

__all__ = ["ReviewSettlementClaim"]


ReviewSettlementClaimStatus = Literal[
    "claimed",
    "settling",
    "promoting",
    "promoted",
    "rejected",
    "failed",
    "missing",
    "invalid",
    "conflict",
]

_REVIEW_SETTLEMENT_STATES = frozenset(
    {
        "pending",
        "settling",
        "promoting",
        "promoted",
        "rejected",
        "failed",
    }
)


@dataclass(frozen=True)
class ReviewSettlementClaim:
    """Durable compare-and-set result for one Review settlement marker."""

    status: ReviewSettlementClaimStatus
    claim_token: str | None = None
    fence_token: int | None = None


@dataclass(frozen=True)
class _ReviewCleanupEntry:
    """Values needed to persist one Review checkpoint cleanup candidate."""

    key: str
    candidate: str
    turn_id: str
    operation: str
    now: str
    staged: bool


@dataclass(frozen=True)
class _ReviewClaimIdentity:
    """Conversation and turn identity for one Review claim."""

    key: str
    turn_id: str


@dataclass(frozen=True)
class _ReviewClaimTiming:
    """Clock inputs for one Review claim transition."""

    now_value: str
    clock: datetime
    stale_after: timedelta


@dataclass(frozen=True)
class _ReviewClaimMarkerRequest:
    """Inputs for one validated Review marker claim transition."""

    connection: sqlite3.Connection
    identity: _ReviewClaimIdentity
    row: sqlite3.Row | tuple[Any, ...]
    decoded: dict[str, Any]
    marker: dict[str, Any]
    timing: _ReviewClaimTiming


@dataclass(frozen=True)
class _ReviewMarkerWriteRequest:
    """Inputs for one private Review marker write."""

    connection: sqlite3.Connection
    key: str
    turn_id: str
    decoded: dict[str, Any]
    marker: Mapping[str, Any]
    now: str


@dataclass(frozen=True)
class _ReviewReservationMarkerRequest:
    """Inputs for advancing one validated Review reservation marker."""

    connection: sqlite3.Connection
    identity: _ReviewClaimIdentity
    row: sqlite3.Row | tuple[Any, ...]
    decoded: dict[str, Any]
    marker: dict[str, Any]
    claim_token: str
    fence_token: int


@dataclass(frozen=True)
class _ReviewClaimLookupRequest:
    """Inputs for precondition checks before a Review marker claim."""

    connection: sqlite3.Connection
    key: str
    row: sqlite3.Row | tuple[Any, ...]
    expected_ledger_version: str | None
    expected_base_context_version: int | None


@dataclass(frozen=True)
class _ReviewFinalizeRequest:
    """Inputs for one validated terminal Review settlement transition."""

    connection: sqlite3.Connection
    identity: _ReviewClaimIdentity
    claim_token: str
    state: Literal["promoted", "rejected", "failed"]
    report_revision: int | None
    fence_token: int | None


def _json(value: dict[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _decode(value: str | None) -> dict[str, Any] | None:
    return None if value is None else json.loads(value)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_REVIEW_SETTLEMENT_CLAIM_TTL = timedelta(minutes=5)
_REVIEW_SETTLEMENT_TOKEN_LIMIT = 64
_REVIEW_SETTLEMENT_TIMESTAMP_LIMIT = 64
_REVIEW_SETTLEMENT_FENCE_LIMIT = 2**63 - 1
_REVIEW_THREAD_ID_LIMIT = 512


def _bounded_stable_thread_id(value: object) -> str | None:
    """Return a validated stable Review thread id, if bounded."""
    if not isinstance(value, str):
        return None
    if len(value) != len("ctx-") + 64:
        return None
    if not value.startswith("ctx-"):
        return None
    if any(char not in "0123456789abcdef" for char in value[4:]):
        return None
    return value


def _bounded_review_id(value: object, *, max_length: int) -> str | None:
    """Return a path-free bounded Review marker id, if valid."""
    if not isinstance(value, str):
        return None
    if not value or len(value) > max_length:
        return None
    if "/" in value or "\\" in value:
        return None
    return value


def _review_candidate_thread_id(marker: Mapping[str, Any]) -> str | None:
    """Return only a deterministic, path-free candidate from a marker."""
    if marker.get("operation") not in {"new_review", "scope_change"}:
        return None
    stable = _bounded_stable_thread_id(marker.get("stable_thread_id"))
    turn_id = _bounded_review_id(marker.get("turn_id"), max_length=64)
    candidate = _bounded_review_id(
        marker.get("candidate_thread_id"),
        max_length=_REVIEW_THREAD_ID_LIMIT,
    )
    if stable is None or turn_id is None or candidate is None:
        return None
    digest = hashlib.sha256(
        f"review-candidate-v1:{stable}:{turn_id}".encode()
    ).hexdigest()[:32]
    expected = f"{stable}:candidate:{digest}"
    return candidate if candidate == expected else None
