# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Atomic staged-turn commit transitions for the context store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .review_support import _decode, _json, _now

if TYPE_CHECKING:
    from .store import StoredBusinessContext

__all__ = [
    "ContextVersionConflictError",
    "ConversationTombstonedError",
    "SettlementResult",
]


class ContextVersionConflictError(RuntimeError):
    """Raised when a turn's base context version is stale."""


class ConversationTombstonedError(RuntimeError):
    """Raised when work is attempted for a deleted conversation."""


@dataclass(frozen=True)
class SettlementResult:
    """Atomic outcome of applying one staged conversation turn."""

    state: Literal["committed", "already_applied"]
    context: StoredBusinessContext


@dataclass(frozen=True)
class _StagedTurnCommitRequest:
    """Inputs for applying one staged turn inside its open transaction."""

    connection: sqlite3.Connection
    key: str
    turn_id: str
    turn: sqlite3.Row | tuple[Any, ...]
    context: sqlite3.Row | tuple[Any, ...] | None
    ledger_version: str
    now: str


@dataclass(frozen=True)
class _CommitCall:
    """Inputs for one public staged-turn commit call."""

    key: str
    turn_id: str
    expected_ledger_version: str
    ledger_version: str
    mutation_lock_held: bool


def _unpack_delta(
    value: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    """Decode a stored turn delta and its optional stage metadata."""
    decoded = _decode(value)
    if decoded is None:
        return (
            None,
            {
                "schema_version": 1,
                "ledger_cursor": 0,
                "observed_mode": "",
            },
            None,
        )
    metadata = decoded.get("__conversation_context_store__")
    if metadata is None:
        return (
            decoded,
            {
                "schema_version": 1,
                "ledger_cursor": 0,
                "observed_mode": "",
            },
            None,
        )
    return decoded["value"], metadata, metadata.get("stage_metadata")


def _apply_staged_turn_locked(
    _store: object, request: _StagedTurnCommitRequest
) -> sqlite3.Row | tuple[Any, ...]:
    """Apply staged data and return the updated context row."""
    data, metadata, _stage_metadata = _unpack_delta(request.turn[8])
    assert data is not None
    schema_version = metadata["schema_version"]
    cursor = metadata["ledger_cursor"]
    mode = metadata["observed_mode"]
    context_data = dict(data)
    if "last_applied_ledger_version" in context_data:
        context_data["last_applied_ledger_version"] = request.ledger_version
    context_json = _json(context_data)
    if request.context is None:
        request.connection.execute(
            "INSERT INTO conversation_contexts VALUES "
            "(?, ?, ?, ?, ?, ?, ?, 'active', 'not_requested', ?, "
            "NULL)",
            (
                request.key,
                schema_version,
                1,
                cursor,
                request.ledger_version,
                mode,
                context_json,
                request.now,
            ),
        )
    else:
        request.connection.execute(
            "UPDATE conversation_contexts SET schema_version=?, "
            "context_version=?, ledger_cursor=?, ledger_version=?, "
            "observed_mode=?, context_json=?, state='active', "
            "updated_at=? WHERE conversation_key=? "
            "AND context_version=?",
            (
                schema_version,
                request.context[2] + 1,
                cursor,
                request.ledger_version,
                mode,
                context_json,
                request.now,
                request.key,
                request.turn[3],
            ),
        )
    request.connection.execute(
        "UPDATE conversation_turns SET state='committed', "
        "ledger_version=?, updated_at=? WHERE conversation_key=? "
        "AND turn_id=?",
        (
            request.ledger_version,
            request.now,
            request.key,
            request.turn_id,
        ),
    )
    return request.connection.execute(
        "SELECT conversation_key, schema_version, context_version, "
        "ledger_cursor, ledger_version, observed_mode, context_json, "
        "state, checkpoint_cleanup_state, updated_at, tombstoned_at "
        "FROM conversation_contexts WHERE conversation_key=?",
        (request.key,),
    ).fetchone()


def _commit_staged_turn(self, request: _CommitCall) -> SettlementResult:
    """Atomically apply a staged turn or return its existing commit."""
    if not request.mutation_lock_held:
        with getattr(self, "acquire_review_mutation_lock")():
            return getattr(self, "commit_staged_turn")(
                request.key,
                request.turn_id,
                request.expected_ledger_version,
                request.ledger_version,
                mutation_lock_held=True,
            )
    now = _now()
    with getattr(self, "_write")() as connection:
        turn = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, ledger_version, "
            "created_at, updated_at, expires_at "
            "FROM conversation_turns WHERE conversation_key=? "
            "AND turn_id=?",
            (request.key, request.turn_id),
        ).fetchone()
        if turn is None:
            raise KeyError((request.key, request.turn_id))
        if turn[4] not in {"staged", "committed"}:
            raise ContextVersionConflictError(request.key)
        if turn[9] != request.expected_ledger_version:
            raise ContextVersionConflictError(request.key)
        review_record = getattr(self, "_review_record")(turn[8])
        if review_record is not None:
            _decoded_review, review_marker = review_record
            if (
                review_marker.get("settlement_state") != "promoted"
                or review_marker.get("settlement_ledger_version")
                != request.expected_ledger_version
                or review_marker.get("settlement_base_context_version")
                != turn[3]
                or getattr(self, "_marker_fence")(review_marker) is None
            ):
                raise ContextVersionConflictError(request.key)
        context = connection.execute(
            "SELECT conversation_key, schema_version, context_version, "
            "ledger_cursor, ledger_version, observed_mode, context_json, "
            "state, checkpoint_cleanup_state, updated_at, tombstoned_at "
            "FROM conversation_contexts WHERE conversation_key=?",
            (request.key,),
        ).fetchone()
        if context is not None and context[7] == "tombstoned":
            raise ConversationTombstonedError(request.key)
        if turn[4] == "committed":
            if context is None:
                raise ContextVersionConflictError(request.key)
            return SettlementResult(
                "already_applied", getattr(self, "_context")(context)
            )
        current = 0 if context is None else context[2]
        if current != turn[3]:
            raise ContextVersionConflictError(request.key)
        row = getattr(self, "_apply_staged_turn_locked")(
            _StagedTurnCommitRequest(
                connection=connection,
                key=request.key,
                turn_id=request.turn_id,
                turn=turn,
                context=context,
                ledger_version=request.ledger_version,
                now=now,
            )
        )
    return SettlementResult("committed", getattr(self, "_context")(row))
