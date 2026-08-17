# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review reservation input, row, and marker-state edges."""

# pylint: disable=protected-access

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast

import pytest

from mcp_server_phytomni.runtime.conversation_context import (
    review_reservation as review_res,
)
from mcp_server_phytomni.runtime.conversation_context.review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    ReviewSettlementClaim,
    _ReviewClaimIdentity,
    _ReviewClaimLookupRequest,
    _ReviewReservationMarkerRequest,
)

_reserve_marker_state = review_res._reserve_marker_state
_reserve_review_settlement = review_res._reserve_review_settlement
_reserve_row_failure = review_res._reserve_row_failure
_review_reservation_inputs_valid = review_res._review_reservation_inputs_valid
_ReviewReservationRequest = review_res._ReviewReservationRequest

pytestmark = pytest.mark.unit


def _lookup(
    row: tuple[object, ...],
    *,
    expected_ledger_version: str | None = None,
    expected_base_context_version: int | None = None,
) -> _ReviewClaimLookupRequest:
    """Build one reservation row-precondition request."""
    return _ReviewClaimLookupRequest(
        connection=cast(sqlite3.Connection, object()),
        key="k",
        row=row,
        expected_ledger_version=expected_ledger_version,
        expected_base_context_version=expected_base_context_version,
    )


def _marker_request(
    marker: dict[str, object],
    *,
    claim_token: str = "tok",
    fence_token: int = 2,
    row: tuple[object, ...] = ("staged", "ledger", 3, "{}"),
) -> _ReviewReservationMarkerRequest:
    """Build one reservation-marker request for the helper under test."""
    return _ReviewReservationMarkerRequest(
        connection=cast(sqlite3.Connection, object()),
        identity=_ReviewClaimIdentity(key="k", turn_id="1"),
        row=row,
        decoded={},
        marker=marker,
        claim_token=claim_token,
        fence_token=fence_token,
    )


def test_review_reservation_inputs_cover_token_and_fence_bounds() -> None:
    """Empty, overlong, and out-of-range tokens stay invalid."""
    assert _review_reservation_inputs_valid("token", 1) is True
    assert _review_reservation_inputs_valid(1, 1) is False
    assert _review_reservation_inputs_valid("", 1) is False
    assert (
        _review_reservation_inputs_valid(
            "t" * (_REVIEW_SETTLEMENT_TOKEN_LIMIT + 1), 1
        )
        is False
    )
    assert _review_reservation_inputs_valid("token", True) is False
    assert _review_reservation_inputs_valid("token", "1") is False
    assert _review_reservation_inputs_valid("token", 0) is False
    assert (
        _review_reservation_inputs_valid(
            "token", _REVIEW_SETTLEMENT_FENCE_LIMIT + 1
        )
        is False
    )


def test_reserve_row_failure_maps_committed_and_version_edges() -> None:
    """Committed, missing-record, and version mismatches stay typed."""
    store = SimpleNamespace(
        _review_context_state=lambda *_args: (1, "tombstoned"),
        _review_record=lambda *_args: None,
    )
    tombstoned = _reserve_row_failure(store, _lookup(("staged", "v", 0, "{}")))
    assert tombstoned is not None and tombstoned.status == "conflict"
    store._review_context_state = lambda *_args: (0, "active")
    committed = _reserve_row_failure(
        store, _lookup(("committed", "v", 0, "{}"))
    )
    assert committed is not None and committed.status == "conflict"
    store._review_record = lambda *_args: (
        "x",
        {"settlement_state": "settling"},
    )
    settling = _reserve_row_failure(
        store, _lookup(("committed", "v", 0, "{}"))
    )
    assert settling is not None and settling.status == "conflict"
    store._review_record = lambda *_args: (
        "x",
        {"settlement_state": "promoted"},
    )
    assert _reserve_row_failure(
        store, _lookup(("committed", "v", 0, "{}"))
    ) == ReviewSettlementClaim("promoted")
    failed = _reserve_row_failure(store, _lookup(("failed", "v", 0, "{}")))
    assert failed is not None and failed.status == "conflict"
    store._review_context_state = lambda *_args: None
    ledger = _reserve_row_failure(
        store,
        _lookup(
            ("staged", "other", 0, "{}"),
            expected_ledger_version="ledger",
        ),
    )
    assert ledger is not None and ledger.status == "conflict"
    version = _reserve_row_failure(
        store,
        _lookup(
            ("staged", "ledger", 1, "{}"),
            expected_base_context_version=0,
        ),
    )
    assert version is not None and version.status == "conflict"
    current = _reserve_row_failure(
        store, _lookup(("staged", "ledger", 1, "{}"))
    )
    assert current is not None and current.status == "conflict"
    assert (
        _reserve_row_failure(store, _lookup(("staged", "ledger", 0, "{}")))
        is None
    )


def test_reserve_marker_state_covers_promoting_and_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promoting matches, terminal states, and write faults stay typed."""
    store = SimpleNamespace(_marker_fence=lambda marker: marker.get("fence"))
    matching = _reserve_marker_state(
        store,
        _marker_request(
            {
                "settlement_state": "promoting",
                "settlement_claim_token": "tok",
                "fence": 2,
            }
        ),
    )
    assert matching == ReviewSettlementClaim("promoting", "tok", 2)
    conflicted = _reserve_marker_state(
        store,
        _marker_request(
            {
                "settlement_state": "promoting",
                "settlement_claim_token": "other",
                "fence": 2,
            }
        ),
    )
    assert conflicted == ReviewSettlementClaim("conflict")
    assert _reserve_marker_state(
        store, _marker_request({"settlement_state": "rejected"})
    ) == ReviewSettlementClaim("rejected")
    assert _reserve_marker_state(
        store, _marker_request({"settlement_state": "failed"})
    ) == ReviewSettlementClaim("failed")
    assert _reserve_marker_state(
        store, _marker_request({"settlement_state": "unknown"})
    ) == ReviewSettlementClaim("conflict")
    settling = _marker_request(
        {
            "settlement_state": "settling",
            "settlement_claim_token": "tok",
            "fence": 2,
            "settlement_ledger_version": "ledger",
            "settlement_base_context_version": 3,
        }
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.conversation_context."
        "review_reservation._write_reservation_marker",
        lambda *_args: False,
    )
    assert _reserve_marker_state(store, settling) == ReviewSettlementClaim(
        "invalid"
    )
    mismatched = _reserve_marker_state(
        store,
        _marker_request(
            {
                "settlement_state": "settling",
                "settlement_claim_token": "tok",
                "fence": 9,
                "settlement_ledger_version": "ledger",
                "settlement_base_context_version": 3,
            }
        ),
    )
    assert mismatched == ReviewSettlementClaim("conflict")


def test_reserve_review_settlement_invalid_and_row_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid tokens and row-precondition claims return without writing."""
    request = _ReviewReservationRequest(
        key="k",
        turn_id="t",
        claim_token="",
        fence_token=1,
        expected_ledger_version=None,
        expected_base_context_version=None,
    )
    store = SimpleNamespace(
        _review_reservation_inputs_valid=_review_reservation_inputs_valid,
        _write=lambda: nullcontext(object()),
    )
    assert _reserve_review_settlement(store, request) == ReviewSettlementClaim(
        "invalid"
    )
    valid = _ReviewReservationRequest(
        key="k",
        turn_id="t",
        claim_token="tok",
        fence_token=1,
        expected_ledger_version=None,
        expected_base_context_version=None,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.conversation_context."
        "review_reservation._review_marker_context_for",
        lambda *_args: ReviewSettlementClaim("conflict"),
    )
    assert _reserve_review_settlement(store, valid) == ReviewSettlementClaim(
        "conflict"
    )
