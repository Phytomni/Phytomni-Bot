# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private Review marker seams installed on the durable context store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from .review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TIMESTAMP_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    ReviewSettlementClaim,
    _bounded_review_id,
    _bounded_stable_thread_id,
    _decode,
    _json,
    _review_candidate_thread_id,
    _ReviewClaimLookupRequest,
    _ReviewMarkerWriteRequest,
)

__all__ = [
    "_bounded_claim_parts",
    "_bounded_marker_fields",
    "_claim_datetime",
    "_claim_is_expired",
    "_claim_row_failure",
    "_marker_candidate_is_bounded",
    "_marker_fence",
    "_marker_is_bounded",
    "_marker_operation",
    "_marker_stable_thread_id",
    "_report_revision_is_bounded",
    "_review_context_state",
    "_review_record",
    "_stable_marker_matches_key",
    "_turn_id_is_bounded",
    "_with_review_record",
    "_write_review_marker",
]


def _review_context_state(
    connection: sqlite3.Connection, key: str
) -> sqlite3.Row | tuple[Any, ...] | None:
    """Read the context version/state used by Review row preconditions."""
    return connection.execute(
        "SELECT context_version, state FROM conversation_contexts "
        "WHERE conversation_key = ?",
        (key,),
    ).fetchone()


def _review_record(
    delta_json: str | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the decoded delta and private Review marker."""
    try:
        decoded = _decode(delta_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    envelope = decoded.get("__conversation_context_store__")
    if not isinstance(envelope, Mapping):
        return None
    stage_metadata = envelope.get("stage_metadata")
    if not isinstance(stage_metadata, Mapping):
        return None
    marker = stage_metadata.get("_review_settlement")
    if not isinstance(marker, Mapping):
        return None
    return decoded, dict(marker)


def _with_review_record(
    decoded: dict[str, Any], marker: Mapping[str, Any]
) -> str | None:
    """Replace the private Review marker while retaining delta bytes."""
    envelope = decoded.get("__conversation_context_store__")
    if not isinstance(envelope, Mapping):
        return None
    stage_metadata = envelope.get("stage_metadata")
    if not isinstance(stage_metadata, Mapping):
        return None
    new_stage_metadata = dict(stage_metadata)
    new_stage_metadata["_review_settlement"] = dict(marker)
    new_envelope = dict(envelope)
    new_envelope["stage_metadata"] = new_stage_metadata
    new_decoded = dict(decoded)
    new_decoded["__conversation_context_store__"] = new_envelope
    return _json(new_decoded)


def _claim_datetime(value: datetime | str | None) -> datetime:
    """Normalize a testable claim clock to UTC."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _write_review_marker(
    _cls,
    request: _ReviewMarkerWriteRequest,
) -> bool:
    delta_json = _with_review_record(request.decoded, request.marker)
    if delta_json is None:
        return False
    request.connection.execute(
        "UPDATE conversation_turns SET delta_json = ?, updated_at = ? "
        "WHERE conversation_key = ? AND turn_id = ?",
        (delta_json, request.now, request.key, request.turn_id),
    )
    return True


def _marker_fence(marker: Mapping[str, Any]) -> int | None:
    """Return a bounded monotonic fencing token from private metadata."""
    value = marker.get("settlement_fence")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > _REVIEW_SETTLEMENT_FENCE_LIMIT
    ):
        return None
    return value


def _marker_operation(marker: Mapping[str, Any]) -> str | None:
    """Return a supported Review operation from one marker."""
    if marker.get("version") != 1:
        return None
    operation = marker.get("operation")
    if operation not in {
        "new_review",
        "follow_up",
        "local_revision",
        "scope_change",
    }:
        return None
    return operation


def _marker_stable_thread_id(marker: Mapping[str, Any]) -> str | None:
    """Return a bounded stable Review thread identity."""
    return _bounded_stable_thread_id(marker.get("stable_thread_id"))


def _turn_id_is_bounded(turn_id: str) -> bool:
    """Reject turn identifiers that could escape the bounded marker."""
    return _bounded_review_id(turn_id, max_length=64) is not None


def _report_revision_is_bounded(marker: Mapping[str, Any]) -> bool:
    """Reject malformed Review report revisions."""
    report_revision = marker.get("report_revision")
    return (
        report_revision is not None
        and not isinstance(report_revision, bool)
        and isinstance(report_revision, int)
        and report_revision >= 0
    )


def _bounded_marker_fields(
    _cls, marker: Mapping[str, Any], turn_id: str
) -> tuple[str, str] | None:
    """Validate marker fields shared by every Review settlement state."""
    operation = _marker_operation(marker)
    stable = _marker_stable_thread_id(marker)
    if operation is None or stable is None:
        return None
    if marker.get("turn_id") != turn_id:
        return None
    if not _turn_id_is_bounded(turn_id):
        return None
    if not _report_revision_is_bounded(marker):
        return None
    return operation, stable


def _stable_marker_matches_key(stable: str, key: str) -> bool:
    """Check that a marker belongs to this conversation's Review agent."""
    try:
        expected_stable = (
            "ctx-"
            + hashlib.sha256(
                f"conversation-context-v1:{UUID(key)}:ReviewAgent".encode(
                    "ascii"
                )
            ).hexdigest()
        )
    except (ValueError, AttributeError):
        return True
    return stable == expected_stable


def _marker_candidate_is_bounded(
    marker: Mapping[str, Any], operation: str
) -> bool:
    """Validate candidate identity only for candidate-producing states."""
    candidate = marker.get("candidate_thread_id")
    if operation in {"new_review", "scope_change"}:
        return _review_candidate_thread_id(marker) is not None
    return candidate is None


def _marker_is_bounded(
    _cls, marker: Mapping[str, Any], *, key: str, turn_id: str
) -> bool:
    """Reject marker identities that cannot belong to this staged row."""
    fields = _bounded_marker_fields(_cls, marker, turn_id)
    if fields is None:
        return False
    operation, stable = fields
    return _stable_marker_matches_key(
        stable, key
    ) and _marker_candidate_is_bounded(marker, operation)


def _claim_is_expired(
    claimed_at: str, *, clock: datetime, stale_after: timedelta
) -> bool:
    try:
        claimed_clock = _claim_datetime(claimed_at)
    except (TypeError, ValueError):
        return True
    return clock - claimed_clock >= stale_after


def _claim_row_failure(
    _self, request: _ReviewClaimLookupRequest
) -> ReviewSettlementClaim | None:
    """Return the first durable row precondition failure, if any."""
    context = _review_context_state(request.connection, request.key)
    failure: ReviewSettlementClaim | None = None
    if context is not None and context[1] == "tombstoned":
        failure = ReviewSettlementClaim("conflict")
    if failure is None and request.row[0] not in {"staged", "committed"}:
        failure = ReviewSettlementClaim("conflict")
    if (
        failure is None
        and request.expected_ledger_version is not None
        and request.row[1] != request.expected_ledger_version
    ):
        failure = ReviewSettlementClaim("conflict")
    if (
        failure is None
        and request.expected_base_context_version is not None
        and request.row[2] != request.expected_base_context_version
    ):
        failure = ReviewSettlementClaim("conflict")
    if failure is None and request.row[0] == "staged":
        current_version = 0 if context is None else context[0]
        if current_version != request.row[2]:
            failure = ReviewSettlementClaim("conflict")
    return failure


def _bounded_claim_parts(
    token: object, claimed_at: object, fence: int | None
) -> tuple[str, str, int] | None:
    """Return validated token parts for an active claim."""
    if not isinstance(token, str) or not token:
        return None
    if len(token) > _REVIEW_SETTLEMENT_TOKEN_LIMIT:
        return None
    if not isinstance(claimed_at, str) or not claimed_at:
        return None
    if len(claimed_at) > _REVIEW_SETTLEMENT_TIMESTAMP_LIMIT:
        return None
    if fence is None:
        return None
    return token, claimed_at, fence
