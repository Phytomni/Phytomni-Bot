# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review settlement claim transitions for the durable context store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_STATES,
    ReviewSettlementClaim,
    ReviewSettlementClaimStatus,
    _now,
    _ReviewClaimIdentity,
    _ReviewClaimLookupRequest,
    _ReviewClaimMarkerRequest,
    _ReviewClaimTiming,
    _ReviewMarkerWriteRequest,
    _ReviewReservationMarkerRequest,
)

__all__ = []


@dataclass(frozen=True)
class _ReviewClaimRequest:
    """Inputs for one staged Review claim lookup."""

    key: str
    turn_id: str
    now: datetime | str | None
    stale_after: timedelta
    expected_ledger_version: str | None
    expected_base_context_version: int | None


@dataclass(frozen=True)
class _ReviewMarkerContext:
    """Shared row and marker values for one Review transition."""

    connection: Any
    identity: _ReviewClaimIdentity
    row: Any
    decoded: dict[str, Any]
    marker: dict[str, Any]


def _review_marker_context(
    connection: Any,
    identity: _ReviewClaimIdentity,
    row: Any,
    decoded: dict[str, Any],
    marker: dict[str, Any],
) -> _ReviewMarkerContext:
    """Build shared row and marker context for a Review transition."""
    return _ReviewMarkerContext(
        connection=connection,
        identity=identity,
        row=row,
        decoded=decoded,
        marker=marker,
    )


def _review_turn_row(
    connection: Any, key: str, turn_id: str
) -> Any:
    """Load the bounded state row used by claim and reservation paths."""
    return connection.execute(
        "SELECT state, ledger_version, base_context_version, "
        "delta_json "
        "FROM conversation_turns "
        "WHERE conversation_key = ? "
        "AND turn_id = ?",
        (key, turn_id),
    ).fetchone()


def _review_claim_lookup(
    connection: Any,
    key: str,
    row: Any,
    expected_ledger_version: str | None,
    expected_base_context_version: int | None,
) -> _ReviewClaimLookupRequest:
    """Build the shared row-precondition request."""
    return _ReviewClaimLookupRequest(
        connection=connection,
        key=key,
        row=row,
        expected_ledger_version=expected_ledger_version,
        expected_base_context_version=expected_base_context_version,
    )


def _review_marker_context_for(
    self, connection: Any, request: Any, failure_method: str
) -> ReviewSettlementClaim | _ReviewMarkerContext:
    """Load and validate one staged Review marker for a transition."""
    row = _review_turn_row(connection, request.key, request.turn_id)
    if row is None:
        return ReviewSettlementClaim("missing")
    failure = getattr(self, failure_method)(
        _review_claim_lookup(
            connection,
            request.key,
            row,
            request.expected_ledger_version,
            request.expected_base_context_version,
        )
    )
    if failure is not None:
        return failure
    record = getattr(self, "_review_record")(row[3])
    if record is None:
        return ReviewSettlementClaim("invalid")
    decoded, marker = record
    if not getattr(self, "_marker_is_bounded")(
        marker, key=request.key, turn_id=request.turn_id
    ):
        return ReviewSettlementClaim("invalid")
    identity = _ReviewClaimIdentity(
        key=request.key, turn_id=request.turn_id
    )
    return _review_marker_context(
        connection, identity, row, decoded, marker
    )


def _claim_marker_request(
    context: _ReviewMarkerContext,
    timing: _ReviewClaimTiming,
) -> _ReviewClaimMarkerRequest:
    """Build the claim transition request from shared marker context."""
    return _ReviewClaimMarkerRequest(
        connection=context.connection,
        identity=context.identity,
        row=context.row,
        decoded=context.decoded,
        marker=context.marker,
        timing=timing,
    )


def _reservation_marker_request(
    context: _ReviewMarkerContext,
    claim_token: str,
    fence_token: int,
) -> _ReviewReservationMarkerRequest:
    """Build the reservation transition request from shared marker context."""
    return _ReviewReservationMarkerRequest(
        connection=context.connection,
        identity=context.identity,
        row=context.row,
        decoded=context.decoded,
        marker=context.marker,
        claim_token=claim_token,
        fence_token=fence_token,
    )


def _write_reservation_marker(
    self,
    request: _ReviewReservationMarkerRequest,
    marker: dict[str, Any],
) -> bool:
    """Persist a reservation marker through the store's dynamic seam."""
    return getattr(self, "_write_review_marker")(
        _ReviewMarkerWriteRequest(
            connection=request.connection,
            key=request.identity.key,
            turn_id=request.identity.turn_id,
            decoded=request.decoded,
            marker=marker,
            now=_now(),
        )
    )


def _claim_active_marker(
    self,
    request: _ReviewClaimMarkerRequest,
    state: ReviewSettlementClaimStatus,
) -> ReviewSettlementClaim:
    """Refresh an expired settling/promoting marker claim."""
    parts = getattr(self, "_bounded_claim_parts")(
        request.marker.get("settlement_claim_token"),
        request.marker.get("settlement_claimed_at"),
        getattr(self, "_marker_fence")(request.marker),
    )
    if parts is None:
        return ReviewSettlementClaim("invalid")
    token, claimed_at, fence = parts
    if not getattr(self, "_claim_is_expired")(
        claimed_at,
        clock=request.timing.clock,
        stale_after=request.timing.stale_after,
    ):
        return ReviewSettlementClaim(state, token, fence)
    next_fence = fence + 1
    if next_fence > _REVIEW_SETTLEMENT_FENCE_LIMIT:
        return ReviewSettlementClaim("invalid")
    claim_token = uuid4().hex
    updated = dict(request.marker)
    updated.update(
        {
            "settlement_state": "settling",
            "settlement_claim_token": claim_token,
            "settlement_claimed_at": request.timing.now_value,
            "settlement_fence": next_fence,
            "settlement_ledger_version": request.row[1],
            "settlement_base_context_version": request.row[2],
        }
    )
    if not getattr(self, "_write_review_marker")(
        _ReviewMarkerWriteRequest(
            connection=request.connection,
            key=request.identity.key,
            turn_id=request.identity.turn_id,
            decoded=request.decoded,
            marker=updated,
            now=request.timing.now_value,
        )
    ):
        return ReviewSettlementClaim("invalid")
    return ReviewSettlementClaim("claimed", claim_token, next_fence)


def _claim_pending_marker(
    self, request: _ReviewClaimMarkerRequest
) -> ReviewSettlementClaim:
    """Create the first claim for a pending marker."""
    if "settlement_claim_token" in request.marker or (
        "settlement_claimed_at" in request.marker
    ):
        return ReviewSettlementClaim("invalid")
    previous_fence = request.marker.get("settlement_fence", 0)
    if (
        isinstance(previous_fence, bool)
        or not isinstance(previous_fence, int)
        or previous_fence < 0
        or previous_fence >= _REVIEW_SETTLEMENT_FENCE_LIMIT
    ):
        return ReviewSettlementClaim("invalid")
    claim_token = uuid4().hex
    fence = previous_fence + 1
    updated = dict(request.marker)
    updated.update(
        {
            "settlement_state": "settling",
            "settlement_claim_token": claim_token,
            "settlement_claimed_at": request.timing.now_value,
            "settlement_fence": fence,
            "settlement_ledger_version": request.row[1],
            "settlement_base_context_version": request.row[2],
        }
    )
    if not getattr(self, "_write_review_marker")(
        _ReviewMarkerWriteRequest(
            connection=request.connection,
            key=request.identity.key,
            turn_id=request.identity.turn_id,
            decoded=request.decoded,
            marker=updated,
            now=request.timing.now_value,
        )
    ):
        return ReviewSettlementClaim("invalid")
    return ReviewSettlementClaim("claimed", claim_token, fence)


def _claim_review_marker(
    self, request: _ReviewClaimMarkerRequest
) -> ReviewSettlementClaim:
    """Advance a validated Review marker under its open transaction."""
    state = request.marker.get("settlement_state")
    if state not in _REVIEW_SETTLEMENT_STATES:
        return ReviewSettlementClaim("invalid")
    if state in {"promoted", "rejected", "failed"}:
        return ReviewSettlementClaim(state)
    if state in {"settling", "promoting"}:
        return getattr(self, "_claim_active_marker")(request, state)
    return getattr(self, "_claim_pending_marker")(request)


def _claim_review_settlement(
    self, request: _ReviewClaimRequest
) -> ReviewSettlementClaim:
    """Claim a staged Review marker with a durable compare-and-set."""
    clock = getattr(self, "_claim_datetime")(request.now)
    now_value = clock.isoformat()
    with getattr(self, "_write")() as connection:
        context = _review_marker_context_for(
            self, connection, request, "_claim_row_failure"
        )
        if isinstance(context, ReviewSettlementClaim):
            return context
        return getattr(self, "_claim_review_marker")(
            _claim_marker_request(
                context,
                _ReviewClaimTiming(
                    now_value=now_value,
                    clock=clock,
                    stale_after=request.stale_after,
                ),
            )
        )
