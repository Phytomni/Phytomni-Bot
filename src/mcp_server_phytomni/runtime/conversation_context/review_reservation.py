# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review settlement reservation transitions for the context store."""

from __future__ import annotations

from dataclasses import dataclass

from .review_claim import (
    _reservation_marker_request,
    _review_marker_context_for,
    _write_reservation_marker,
)
from .review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    ReviewSettlementClaim,
    _ReviewClaimLookupRequest,
    _ReviewReservationMarkerRequest,
)

__all__ = []


@dataclass(frozen=True)
class _ReviewReservationRequest:
    """Inputs for one staged Review reservation lookup."""

    key: str
    turn_id: str
    claim_token: str
    fence_token: int
    expected_ledger_version: str | None
    expected_base_context_version: int | None


def _reserve_row_failure(
    self, request: _ReviewClaimLookupRequest
) -> ReviewSettlementClaim | None:
    """Return the first row precondition failure for reservation."""
    context = getattr(self, "_review_context_state")(
        request.connection, request.key
    )
    if context is not None and context[1] == "tombstoned":
        return ReviewSettlementClaim("conflict")
    if request.row[0] != "staged":
        if request.row[0] == "committed":
            record = getattr(self, "_review_record")(request.row[3])
            if (
                record is not None
                and record[1].get("settlement_state") == "promoted"
            ):
                return ReviewSettlementClaim("promoted")
        return ReviewSettlementClaim("conflict")
    if (
        request.expected_ledger_version is not None
        and request.row[1] != request.expected_ledger_version
    ) or (
        request.expected_base_context_version is not None
        and request.row[2] != request.expected_base_context_version
    ):
        return ReviewSettlementClaim("conflict")
    current_version = 0 if context is None else context[0]
    if current_version != request.row[2]:
        return ReviewSettlementClaim("conflict")
    return None


def _review_reservation_inputs_valid(
    claim_token: object, fence_token: object
) -> bool:
    """Validate reservation tokens before opening the write transaction."""
    claim_value: str | None = (
        claim_token
        if isinstance(claim_token, str)
        and bool(claim_token)
        and len(claim_token) <= _REVIEW_SETTLEMENT_TOKEN_LIMIT
        else None
    )
    fence_value: int | None = (
        fence_token
        if not isinstance(fence_token, bool) and isinstance(fence_token, int)
        else None
    )
    return claim_value is not None and (
        fence_value is not None
        and 1 <= fence_value <= _REVIEW_SETTLEMENT_FENCE_LIMIT
    )


def _reserve_marker_state(
    self, request: _ReviewReservationMarkerRequest
) -> ReviewSettlementClaim:
    """Advance a validated reservation marker in its open transaction."""
    state = request.marker.get("settlement_state")
    claim_matches = (
        request.marker.get("settlement_claim_token") == request.claim_token
        and getattr(self, "_marker_fence")(request.marker)
        == request.fence_token
    )
    if state == "promoting":
        return ReviewSettlementClaim(
            "promoting" if claim_matches else "conflict",
            request.claim_token if claim_matches else None,
            request.fence_token if claim_matches else None,
        )
    if state != "settling":
        if state in {"promoted", "rejected", "failed"}:
            return ReviewSettlementClaim(state)
        return ReviewSettlementClaim("conflict")
    if (
        not claim_matches
        or request.marker.get("settlement_ledger_version") != request.row[1]
        or request.marker.get("settlement_base_context_version")
        != request.row[2]
    ):
        return ReviewSettlementClaim("conflict")
    updated = dict(request.marker)
    updated["settlement_state"] = "promoting"
    if not _write_reservation_marker(self, request, updated):
        return ReviewSettlementClaim("invalid")
    return ReviewSettlementClaim(
        "promoting", request.claim_token, request.fence_token
    )


def _reserve_review_settlement(
    self, request: _ReviewReservationRequest
) -> ReviewSettlementClaim:
    """Reserve a staged proposal before a private checkpoint write."""
    if not getattr(self, "_review_reservation_inputs_valid")(
        request.claim_token, request.fence_token
    ):
        return ReviewSettlementClaim("invalid")
    with getattr(self, "_write")() as connection:
        context = _review_marker_context_for(
            self, connection, request, "_reserve_row_failure"
        )
        if isinstance(context, ReviewSettlementClaim):
            return context
        return getattr(self, "_reserve_marker_state")(
            _reservation_marker_request(
                context, request.claim_token, request.fence_token
            )
        )
