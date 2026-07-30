# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review settlement finalization transitions for the context store."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .review_claim import _review_marker_write_request
from .review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    _now,
    _ReviewClaimIdentity,
    _ReviewFinalizeRequest,
)

__all__ = []


@dataclass(frozen=True)
class _ReviewFinalizeCall:
    """Inputs for one validated terminal Review transition."""

    key: str
    turn_id: str
    claim_token: str
    state: Literal["promoted", "rejected", "failed"]
    report_revision: int | None
    fence_token: int | None


@dataclass(frozen=True)
class _ReviewFailureCall:
    """Inputs for one terminal Review failure transition."""

    key: str
    turn_id: str
    mutation_lock_held: bool


@dataclass(frozen=True)
class _ReviewMetadataCall:
    """Inputs for one private Review metadata update."""

    key: str
    turn_id: str
    updates: Mapping[str, Any]


def _review_delta_row(connection: Any, key: str, turn_id: str) -> Any:
    """Load one turn delta for a Review marker transition."""
    return connection.execute(
        "SELECT delta_json FROM conversation_turns "
        "WHERE conversation_key = ? AND turn_id = ?",
        (key, turn_id),
    ).fetchone()


def _finalize_review_settlement_locked(
    self, request: _ReviewFinalizeRequest
) -> bool:
    """Persist one terminal Review state inside the open transaction."""
    row = _review_delta_row(
        request.connection, request.identity.key, request.identity.turn_id
    )
    if row is None:
        return False
    record = getattr(self, "_review_record")(row[0])
    if record is None:
        return False
    decoded, marker = record
    current_state = marker.get("settlement_state")
    if current_state == request.state:
        return True
    if current_state in {"promoted", "rejected", "failed"}:
        return current_state == request.state
    if (
        current_state not in {"settling", "promoting"}
        or marker.get("settlement_claim_token") != request.claim_token
        or (
            request.fence_token is not None
            and marker.get("settlement_fence") != request.fence_token
        )
    ):
        return False
    updated = dict(marker)
    updated["settlement_state"] = request.state
    updated.pop("settlement_claim_token", None)
    updated.pop("settlement_claimed_at", None)
    if request.report_revision is not None:
        updated["report_revision"] = request.report_revision
    return getattr(self, "_write_review_marker")(
        _review_marker_write_request(
            request.connection,
            request.identity,
            decoded,
            updated,
            _now(),
        )
    )


def _finalize_review_settlement(
    self, request: _ReviewFinalizeCall
) -> bool:
    """Finalize only the worker that durably claimed a Review marker."""
    if (
        not isinstance(request.claim_token, str)
        or not request.claim_token
        or len(request.claim_token) > _REVIEW_SETTLEMENT_TOKEN_LIMIT
    ):
        return False
    if request.fence_token is not None and (
        isinstance(request.fence_token, bool)
        or not isinstance(request.fence_token, int)
        or request.fence_token < 1
        or request.fence_token > _REVIEW_SETTLEMENT_FENCE_LIMIT
    ):
        return False
    if request.state == "promoted" and (
        request.report_revision is None
        or isinstance(request.report_revision, bool)
        or request.report_revision < 0
    ):
        return False
    with getattr(self, "_write")() as connection:
        return getattr(self, "_finalize_review_settlement_locked")(
            _ReviewFinalizeRequest(
                connection=connection,
                identity=_ReviewClaimIdentity(
                    key=request.key, turn_id=request.turn_id
                ),
                claim_token=request.claim_token,
                state=request.state,
                report_revision=request.report_revision,
                fence_token=request.fence_token,
            )
        )


def _mark_review_settlement_failed(
    self, request: _ReviewFailureCall
) -> bool:
    """Persist a terminal failure for a malformed or abandoned marker."""
    if not request.mutation_lock_held:
        with getattr(self, "acquire_review_mutation_lock")():
            return getattr(self, "mark_review_settlement_failed")(
                request.key, request.turn_id, mutation_lock_held=True
            )
    with getattr(self, "_write")() as connection:
        row = _review_delta_row(connection, request.key, request.turn_id)
        if row is None:
            return False
        record = getattr(self, "_review_record")(row[0])
        if record is None:
            return False
        decoded, marker = record
        state = marker.get("settlement_state")
        if state in {"promoted", "rejected"}:
            return False
        if state == "failed":
            return True
        marker = dict(marker)
        marker["settlement_state"] = "failed"
        marker.pop("settlement_claim_token", None)
        marker.pop("settlement_claimed_at", None)
        return getattr(self, "_write_review_marker")(
            _review_marker_write_request(
                connection,
                _ReviewClaimIdentity(
                    key=request.key, turn_id=request.turn_id
                ),
                decoded,
                marker,
                _now(),
            )
        )


def _update_review_settlement_metadata(
    self, request: _ReviewMetadataCall
) -> bool:
    """Update private Review metadata for compatibility test seams."""
    with getattr(self, "_write")() as connection:
        row = _review_delta_row(connection, request.key, request.turn_id)
        if row is None:
            return False
        record = getattr(self, "_review_record")(row[0])
        if record is None:
            return False
        decoded, marker = record
        marker.update(dict(request.updates))
        return getattr(self, "_write_review_marker")(
            _review_marker_write_request(
                connection,
                _ReviewClaimIdentity(
                    key=request.key, turn_id=request.turn_id
                ),
                decoded,
                marker,
                _now(),
            )
        )
