# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Core persistence and staging operations for the context store."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from ...config.defaults import ApiConfig
from .review_support import (
    _decode,
    _json,
    _now,
    _review_candidate_thread_id,
    _ReviewCleanupEntry,
)
from .turn_commit import ConversationTombstonedError, _unpack_delta

logger = logging.getLogger(
    "mcp_server_phytomni.runtime.conversation_context.store"
)


@dataclass(frozen=True)
class _RegisterCandidateCall:
    """Validated public inputs for one Review cleanup registration."""

    key: str
    turn_id: str
    operation: str
    stable_thread_id: str
    candidate_thread_id: str
    mutation_lock_held: bool

    @classmethod
    def from_values(
        cls, values: tuple[str, str, str, str, str, bool]
    ) -> _RegisterCandidateCall:
        """Build a request from the public facade's positional values."""
        key, turn_id, operation, stable_thread_id, candidate, held = values
        return cls(
            key=key,
            turn_id=turn_id,
            operation=operation,
            stable_thread_id=stable_thread_id,
            candidate_thread_id=candidate,
            mutation_lock_held=held,
        )

    def public_args(self) -> tuple[str, str, str, str, str]:
        """Return the positional portion of the historical public call."""
        return (
            self.key,
            self.turn_id,
            self.operation,
            self.stable_thread_id,
            self.candidate_thread_id,
        )


_STORE_TYPES: dict[str, type[Any]] = {}


def configure_store_types(
    *,
    context_type: type[Any],
    turn_type: type[Any],
    begin_turn_type: type[Any],
    conflict_type: type[BaseException],
) -> None:
    """Install public value-object classes without importing the facade."""
    _STORE_TYPES.update(
        {
            "context": context_type,
            "turn": turn_type,
            "begin": begin_turn_type,
            "conflict": conflict_type,
        }
    )


_CREATE_CONTEXTS = """
CREATE TABLE IF NOT EXISTS conversation_contexts (
    conversation_key TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    context_version INTEGER NOT NULL,
    ledger_cursor INTEGER NOT NULL,
    ledger_version TEXT NOT NULL,
    observed_mode TEXT NOT NULL,
    context_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'tombstoned')),
    checkpoint_cleanup_state TEXT NOT NULL CHECK (
        checkpoint_cleanup_state IN ('not_requested', 'pending', 'complete')
    ),
    updated_at TEXT NOT NULL,
    tombstoned_at TEXT
)
"""

_CREATE_TURNS = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    conversation_key TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    base_context_version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('in_progress', 'staged', 'committed', 'failed')
    ),
    selected_agent_id TEXT,
    route_source TEXT,
    result_json TEXT,
    delta_json TEXT,
    ledger_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (conversation_key, turn_id)
)
"""

_CREATE_REVIEW_CHECKPOINT_CLEANUP = """
CREATE TABLE IF NOT EXISTS conversation_review_checkpoint_cleanup (
    conversation_key TEXT NOT NULL,
    candidate_thread_id TEXT NOT NULL,
    turn_id TEXT,
    operation TEXT,
    registered_at TEXT,
    staged_at TEXT,
    eligible_at TEXT,
    tombstone_pending INTEGER NOT NULL DEFAULT 0 CHECK (
        tombstone_pending IN (0, 1)
    ),
    PRIMARY KEY (conversation_key, candidate_thread_id)
)
"""

_REVIEW_CLEANUP_ADD_COLUMN_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "turn_id",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN turn_id TEXT"
        ),
    ),
    (
        "operation",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN operation TEXT"
        ),
    ),
    (
        "registered_at",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN registered_at TEXT"
        ),
    ),
    (
        "staged_at",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN staged_at TEXT"
        ),
    ),
    (
        "eligible_at",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN eligible_at TEXT"
        ),
    ),
    (
        "tombstone_pending",
        (
            "ALTER TABLE conversation_review_checkpoint_cleanup "
            "ADD COLUMN tombstone_pending INTEGER NOT NULL DEFAULT 0"
        ),
    ),
)


def _connection(self: Any):
    """Resolve the store-owned connection seam, including test patches."""
    return getattr(self, "_connection")(self.db_path)


def _pack_delta(staged: Any) -> str:
    """Encode staged metadata with the historical envelope shape."""
    return _json(
        {
            "__conversation_context_store__": {
                "schema_version": staged.schema_version,
                "ledger_cursor": staged.ledger_cursor,
                "observed_mode": staged.observed_mode,
                "stage_metadata": staged.stage_metadata,
            },
            "value": staged.delta,
        }
    )


def _init_db(self: Any) -> None:
    """Create the context, turn, and Review cleanup tables if needed."""
    with _connection(self) as connection:
        connection.execute(_CREATE_CONTEXTS)
        connection.execute(_CREATE_TURNS)
        connection.execute(_CREATE_REVIEW_CHECKPOINT_CLEANUP)
        cleanup_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(conversation_review_checkpoint_cleanup)"
            )
        }
        for column, statement in _REVIEW_CLEANUP_ADD_COLUMN_STATEMENTS:
            if column not in cleanup_columns:
                connection.execute(statement)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_turns_expires_at "
            "ON conversation_turns(expires_at)"
        )


@contextmanager
def write(self: Any):
    """Open a transaction that rolls back on an unsuccessful mutation."""
    with _connection(self) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        connection.commit()


@contextmanager
def _write(self: Any):
    """Compatibility alias for the public transaction context."""
    with self.write() as connection:
        yield connection


def _context(row: sqlite3.Row | tuple[Any, ...]) -> Any:
    """Decode one context row into the historical public value object."""
    return _STORE_TYPES["context"](
        conversation_key=row[0],
        schema_version=row[1],
        context_version=row[2],
        ledger_cursor=row[3],
        ledger_version=row[4],
        observed_mode=row[5],
        context=json.loads(row[6]),
        state=row[7],
        checkpoint_cleanup_state=row[8],
        updated_at=row[9],
        tombstoned_at=row[10],
    )


def _turn(row: tuple[Any, ...]) -> Any:
    """Decode one turn row into the historical public value object."""
    delta, _metadata, stage_metadata = _unpack_delta(row[8])
    return _STORE_TYPES["turn"](
        conversation_key=row[0],
        turn_id=row[1],
        operation=row[2],
        base_context_version=row[3],
        state=row[4],
        selected_agent_id=row[5],
        route_source=row[6],
        result=_decode(row[7]),
        delta=delta,
        stage_metadata=stage_metadata,
        ledger_version=row[9],
        created_at=row[10],
        updated_at=row[11],
        expires_at=row[12],
    )


def load_context(self: Any, key: str):
    """Load one conversation context without mutating its state."""
    with _connection(self) as connection:
        row = connection.execute(
            "SELECT conversation_key, schema_version, context_version, "
            "ledger_cursor, ledger_version, observed_mode, context_json, "
            "state, "
            "checkpoint_cleanup_state, updated_at, tombstoned_at "
            "FROM conversation_contexts WHERE conversation_key = ?",
            (key,),
        ).fetchone()
    return None if row is None else getattr(self, "_context")(row)


def load_turn(self: Any, key: str, turn_id: str):
    """Load one turn without creating a new pending proposal."""
    with _connection(self) as connection:
        row = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, ledger_version, "
            "created_at, updated_at, expires_at FROM conversation_turns "
            "WHERE conversation_key = ? AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
    return None if row is None else getattr(self, "_turn")(row)


def begin_turn(
    self: Any,
    key: str,
    turn_id: str,
    operation: str,
    base_version: int,
):
    """Create or return the durable in-progress record for one turn."""
    now = _now()
    with getattr(self, "_write")() as connection:
        context = connection.execute(
            "SELECT conversation_key, schema_version, context_version, "
            "ledger_cursor, ledger_version, observed_mode, context_json, "
            "state, checkpoint_cleanup_state, updated_at, tombstoned_at "
            "FROM conversation_contexts WHERE conversation_key = ?",
            (key,),
        ).fetchone()
        current_version = 0 if context is None else context[2]
        if context is not None and context[7] == "tombstoned":
            raise ConversationTombstonedError(key)
        existing = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, "
            "result_json, delta_json, ledger_version, created_at, "
            "updated_at, expires_at FROM conversation_turns "
            "WHERE conversation_key = ? AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
        if existing is not None:
            return _STORE_TYPES["begin"](
                False, current_version, getattr(self, "_turn")(existing)
            )
        connection.execute(
            "INSERT INTO conversation_turns "
            "(conversation_key, turn_id, operation, base_context_version, "
            "state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'in_progress', ?, ?)",
            (key, turn_id, operation, base_version, now, now),
        )
        row = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, "
            "ledger_version, created_at, updated_at, expires_at "
            "FROM conversation_turns WHERE conversation_key = ? "
            "AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
    logger.debug("conversation turn begun")
    return _STORE_TYPES["begin"](
        True, current_version, getattr(self, "_turn")(row)
    )


def reopen_turn(
    self: Any,
    key: str,
    turn_id: str,
    operation: str,
    base_version: int,
):
    """Reset one durable turn so replace/rebuild can supersede it."""
    now = _now()
    with getattr(self, "_write")() as connection:
        existing = connection.execute(
            "SELECT conversation_key FROM conversation_turns "
            "WHERE conversation_key = ? AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
        if existing is None:
            raise KeyError((key, turn_id))
        connection.execute(
            "UPDATE conversation_turns SET operation=?, "
            "base_context_version=?, state='in_progress', "
            "selected_agent_id=NULL, route_source=NULL, "
            "result_json=NULL, delta_json=NULL, ledger_version=NULL, "
            "updated_at=?, expires_at=NULL "
            "WHERE conversation_key=? AND turn_id=?",
            (operation, base_version, now, key, turn_id),
        )
        row = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, "
            "ledger_version, created_at, updated_at, expires_at "
            "FROM conversation_turns WHERE conversation_key = ? "
            "AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
    logger.debug("conversation turn reopened")
    return getattr(self, "_turn")(row)


def _upsert_review_checkpoint_cleanup(
    connection: sqlite3.Connection,
    entry: _ReviewCleanupEntry,
) -> None:
    """Record one bounded candidate before or during turn staging."""
    connection.execute(
        "INSERT OR IGNORE INTO conversation_review_checkpoint_cleanup "
        "(conversation_key, candidate_thread_id, turn_id, operation, "
        "registered_at, staged_at, eligible_at, tombstone_pending) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, 0)",
        (
            entry.key,
            entry.candidate,
            entry.turn_id,
            entry.operation,
            entry.now,
            entry.now if entry.staged else None,
        ),
    )
    connection.execute(
        "UPDATE conversation_review_checkpoint_cleanup SET "
        "turn_id = COALESCE(turn_id, ?), "
        "operation = COALESCE(operation, ?), "
        "registered_at = COALESCE(registered_at, ?) "
        "WHERE conversation_key = ? AND candidate_thread_id = ?",
        (
            entry.turn_id,
            entry.operation,
            entry.now,
            entry.key,
            entry.candidate,
        ),
    )
    if entry.staged:
        connection.execute(
            "UPDATE conversation_review_checkpoint_cleanup SET "
            "staged_at = COALESCE(staged_at, ?) "
            "WHERE conversation_key = ? AND candidate_thread_id = ?",
            (entry.now, entry.key, entry.candidate),
        )


def _register_review_candidate_locked(
    self: Any, entry: _ReviewCleanupEntry
) -> bool:
    """Persist a validated candidate while the mutation lock is held."""
    with getattr(self, "_write")() as connection:
        context = connection.execute(
            "SELECT state FROM conversation_contexts "
            "WHERE conversation_key = ?",
            (entry.key,),
        ).fetchone()
        if context is not None and context[0] == "tombstoned":
            return False
        existing = connection.execute(
            "SELECT tombstone_pending, turn_id, operation FROM "
            "conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ? AND candidate_thread_id = ?",
            (entry.key, entry.candidate),
        ).fetchone()
        if existing is not None and existing[0]:
            return False
        if existing is not None and (
            (existing[1] is not None and existing[1] != entry.turn_id)
            or (existing[2] is not None and existing[2] != entry.operation)
        ):
            return False
        getattr(self, "_upsert_review_checkpoint_cleanup")(connection, entry)
    return True


def register_review_candidate(self: Any, call: _RegisterCandidateCall) -> bool:
    """Register a candidate before Review can write its checkpoint."""
    key = call.key
    turn_id = call.turn_id
    operation = call.operation
    stable_thread_id = call.stable_thread_id
    candidate_thread_id = call.candidate_thread_id
    mutation_lock_held = call.mutation_lock_held
    marker = {
        "version": 1,
        "operation": operation,
        "stable_thread_id": stable_thread_id,
        "candidate_thread_id": candidate_thread_id,
        "turn_id": turn_id,
        "report_revision": 0,
        "settlement_state": "pending",
    }
    try:
        UUID(key)
    except (AttributeError, TypeError, ValueError):
        return False
    if operation not in {"new_review", "scope_change"} or not getattr(
        self, "_marker_is_bounded"
    )(marker, key=key, turn_id=turn_id):
        return False
    if not mutation_lock_held:
        with self.acquire_review_mutation_lock():
            return getattr(self, "register_review_candidate")(
                *call.public_args(), mutation_lock_held=True
            )
    return getattr(self, "_register_review_candidate_locked")(
        _ReviewCleanupEntry(
            key=key,
            candidate=candidate_thread_id,
            turn_id=turn_id,
            operation=operation,
            now=_now(),
            staged=False,
        )
    )


def _staged_turn_matches(
    row: sqlite3.Row | tuple[Any, ...],
    staged: Any,
    result_json: str,
    delta_json: str,
) -> bool:
    """Check idempotent staging fields in their existing order."""
    return all(
        value == expected
        for value, expected in (
            (row[2], staged.operation),
            (row[3], staged.base_context_version),
            (row[5], staged.selected_agent_id),
            (row[6], staged.route_source),
            (row[7], result_json),
            (row[8], delta_json),
            (row[9], staged.ledger_version),
        )
    )


def stage_turn(self: Any, key: str, turn_id: str, staged: Any):
    """Persist one terminal proposal, preserving idempotent retries."""
    now = _now()
    expires = (
        datetime.fromisoformat(now)
        + timedelta(hours=ApiConfig().API_RUN_TTL_OK_HOURS)
    ).isoformat()
    result_json, delta_json = _json(staged.result), _pack_delta(staged)
    with getattr(self, "_write")() as connection:
        row = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, "
            "ledger_version, created_at, updated_at, expires_at "
            "FROM conversation_turns WHERE conversation_key = ? "
            "AND turn_id = ?",
            (key, turn_id),
        ).fetchone()
        if row is None:
            raise KeyError((key, turn_id))
        if row[4] in {"staged", "committed"}:
            if not getattr(self, "_staged_turn_matches")(
                row, staged, result_json, delta_json
            ):
                raise _STORE_TYPES["conflict"]((key, turn_id))
            _upsert_staged_cleanup(connection, staged, key, now)
            return getattr(self, "_turn")(row)
        if row[4] != "in_progress":
            raise _STORE_TYPES["conflict"]((key, turn_id))
        connection.execute(
            "UPDATE conversation_turns SET state='staged', "
            "selected_agent_id=?, route_source=?, result_json=?, "
            "delta_json=?, ledger_version=?, updated_at=?, expires_at=? "
            "WHERE conversation_key=? AND turn_id=?",
            (
                staged.selected_agent_id,
                staged.route_source,
                result_json,
                delta_json,
                staged.ledger_version,
                now,
                expires,
                key,
                turn_id,
            ),
        )
        _upsert_staged_cleanup(connection, staged, key, now)
        row = connection.execute(
            "SELECT conversation_key, turn_id, operation, "
            "base_context_version, state, selected_agent_id, "
            "route_source, result_json, delta_json, ledger_version, "
            "created_at, updated_at, expires_at FROM conversation_turns "
            "WHERE conversation_key=? AND turn_id=?",
            (key, turn_id),
        ).fetchone()
    logger.debug("conversation turn staged")
    return getattr(self, "_turn")(row)


def _upsert_staged_cleanup(
    connection: sqlite3.Connection,
    staged: Any,
    key: str,
    now: str,
) -> None:
    """Persist the Review cleanup identity carried by staged metadata."""
    marker = staged.stage_metadata.get("_review_settlement")
    if not isinstance(marker, Mapping):
        return
    candidate = _review_candidate_thread_id(marker)
    operation = marker.get("operation")
    marker_turn_id = marker.get("turn_id")
    if (
        candidate is not None
        and isinstance(operation, str)
        and isinstance(marker_turn_id, str)
    ):
        _upsert_review_checkpoint_cleanup(
            connection,
            _ReviewCleanupEntry(
                key=key,
                candidate=candidate,
                turn_id=marker_turn_id,
                operation=operation,
                now=now,
                staged=True,
            ),
        )


__all__ = [
    "_context",
    "_init_db",
    "_register_review_candidate_locked",
    "_staged_turn_matches",
    "_turn",
    "_upsert_review_checkpoint_cleanup",
    "_write",
    "begin_turn",
    "load_context",
    "load_turn",
    "register_review_candidate",
    "stage_turn",
    "write",
]
