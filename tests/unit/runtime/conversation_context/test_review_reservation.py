# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review reservation precondition and marker-state transitions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.runtime.conversation_context import (
    review_reservation as review_res,
)
from mcp_server_phytomni.runtime.conversation_context.review_support import (
    ReviewSettlementClaim,
    _ReviewClaimIdentity,
    _ReviewClaimLookupRequest,
    _ReviewReservationMarkerRequest,
)

_reserve_marker_state = review_res._reserve_marker_state
_reserve_row_failure = review_res._reserve_row_failure
_review_reservation_inputs_valid = review_res._review_reservation_inputs_valid

pytestmark = pytest.mark.unit


def test_review_reservation_inputs_reject_invalid_tokens() -> None:
    """Empty claim tokens and out-of-range fences are invalid."""
    assert _review_reservation_inputs_valid("token", 1) is True
    assert _review_reservation_inputs_valid("", 1) is False
    assert _review_reservation_inputs_valid("token", True) is False
    assert _review_reservation_inputs_valid("token", 0) is False


def test_reserve_row_failure_maps_tombstone_and_promoted_rows() -> None:
    """Tombstoned context and promoted committed rows stay typed claims."""
    store = SimpleNamespace(
        _review_context_state=lambda *_args: (1, "tombstoned"),
        _review_record=lambda *_args: ("x", {"settlement_state": "promoted"}),
    )
    tombstoned = _reserve_row_failure(
        store,
        _ReviewClaimLookupRequest(
            connection=object(),
            key="k",
            row=("staged", "v", 0, "{}"),
            expected_ledger_version=None,
            expected_base_context_version=None,
        ),
    )
    assert tombstoned == ReviewSettlementClaim("conflict")
    store._review_context_state = lambda *_args: (0, "active")
    promoted = _reserve_row_failure(
        store,
        _ReviewClaimLookupRequest(
            connection=object(),
            key="k",
            row=("committed", "v", 0, "{}"),
            expected_ledger_version=None,
            expected_base_context_version=None,
        ),
    )
    assert promoted == ReviewSettlementClaim("promoted")


def _marker_request(
    marker: dict[str, object],
    *,
    claim_token: str = "tok",
    fence_token: int = 2,
    row: tuple[object, ...] = ("staged", "ledger", 3, "{}"),
) -> _ReviewReservationMarkerRequest:
    """Build one reservation-marker request for the helper under test."""
    return _ReviewReservationMarkerRequest(
        connection=object(),
        identity=_ReviewClaimIdentity(key="k", turn_id="1"),
        row=row,
        decoded={},
        marker=marker,
        claim_token=claim_token,
        fence_token=fence_token,
    )


def test_reserve_marker_state_promotes_matching_settling_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching settling marker advances to promoting."""
    written: list[dict[str, object]] = []
    store = SimpleNamespace(
        _marker_fence=lambda marker: marker["settlement_fence_token"],
    )
    request = _marker_request(
        {
            "settlement_state": "settling",
            "settlement_claim_token": "tok",
            "settlement_fence_token": 2,
            "settlement_ledger_version": "ledger",
            "settlement_base_context_version": 3,
        }
    )

    def _write(
        _self: object, _req: object, updated: dict[str, object]
    ) -> bool:
        written.append(updated)
        return True

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.conversation_context.review_reservation."
        "_write_reservation_marker",
        _write,
    )
    claim = _reserve_marker_state(store, request)
    assert claim.status == "promoting"
    assert written[0]["settlement_state"] == "promoting"


def test_reserve_marker_state_returns_terminal_states() -> None:
    """Promoted or unknown marker states stay non-mutating claims."""
    store = SimpleNamespace(_marker_fence=lambda marker: 1)
    promoted = _reserve_marker_state(
        store, _marker_request({"settlement_state": "promoted"})
    )
    assert promoted == ReviewSettlementClaim("promoted")
    conflict = _reserve_marker_state(
        store, _marker_request({"settlement_state": "unknown"})
    )
    assert conflict == ReviewSettlementClaim("conflict")
