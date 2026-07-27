# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Durable Bot-owned conversation context and terminal-turn staging."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ...config.defaults import ApiConfig
from ..sqlite import sqlite_connection
from ..task_manager import resolve_tasks_db_path

logger = logging.getLogger(__name__)

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


class ContextVersionConflictError(RuntimeError):
    """Raised when a turn's base context version is stale."""


class ConversationTombstonedError(RuntimeError):
    """Raised when work is attempted for a deleted conversation."""


class StagedTurnConflictError(RuntimeError):
    """Raised when a retry proposes different terminal bytes."""


@dataclass(frozen=True)
class StoredBusinessContext:
    conversation_key: str
    schema_version: int
    context_version: int
    ledger_cursor: int
    ledger_version: str
    observed_mode: str
    context: dict[str, Any]
    state: Literal["active", "tombstoned"]
    checkpoint_cleanup_state: Literal["not_requested", "pending", "complete"]
    updated_at: str
    tombstoned_at: str | None


@dataclass(frozen=True)
class StagedTurn:
    operation: str
    base_context_version: int
    selected_agent_id: str
    route_source: str
    result: dict[str, Any]
    delta: dict[str, Any]
    ledger_version: str
    schema_version: int
    ledger_cursor: int
    observed_mode: str
    stage_metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredTurn:
    conversation_key: str
    turn_id: str
    operation: str
    base_context_version: int
    state: Literal["in_progress", "staged", "committed", "failed"]
    selected_agent_id: str | None
    route_source: str | None
    result: dict[str, Any] | None
    delta: dict[str, Any] | None
    stage_metadata: dict[str, Any] | None
    ledger_version: str | None
    created_at: str
    updated_at: str
    expires_at: str | None


@dataclass(frozen=True)
class BeginTurnResult:
    created: bool
    context_version: int
    turn: StoredTurn


@dataclass(frozen=True)
class SettlementResult:
    """Atomic outcome of applying one staged conversation turn."""

    state: Literal["committed", "already_applied"]
    context: StoredBusinessContext


def _json(value: dict[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _decode(value: str | None) -> dict[str, Any] | None:
    return None if value is None else json.loads(value)


def _pack_delta(staged: StagedTurn) -> str:
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


def _unpack_delta(
    value: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
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


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationContextStore:
    """Persist versioned context and one terminal proposal per turn."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or resolve_tasks_db_path()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite_connection(self.db_path) as connection:
            connection.execute(_CREATE_CONTEXTS)
            connection.execute(_CREATE_TURNS)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_turns_expires_at "
                "ON conversation_turns(expires_at)"
            )

    @contextmanager
    def _write(self):
        with sqlite_connection(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _context(row: sqlite3.Row | tuple[Any, ...]) -> StoredBusinessContext:
        return StoredBusinessContext(
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

    @staticmethod
    def _turn(row: tuple[Any, ...]) -> StoredTurn:
        delta, _metadata, stage_metadata = _unpack_delta(row[8])
        return StoredTurn(
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

    def load_context(self, key: str) -> StoredBusinessContext | None:
        with sqlite_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT conversation_key, schema_version, context_version, "
                "ledger_cursor, ledger_version, observed_mode, context_json, state, "
                "checkpoint_cleanup_state, updated_at, tombstoned_at "
                "FROM conversation_contexts WHERE conversation_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else self._context(row)

    def load_turn(self, key: str, turn_id: str) -> StoredTurn | None:
        """Load one turn without creating a new pending proposal."""
        with sqlite_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT conversation_key, turn_id, operation, "
                "base_context_version, state, selected_agent_id, "
                "route_source, result_json, delta_json, ledger_version, "
                "created_at, updated_at, expires_at FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
        return None if row is None else self._turn(row)

    def begin_turn(
        self, key: str, turn_id: str, operation: str, base_version: int
    ) -> BeginTurnResult:
        now = _now()
        with self._write() as connection:
            context = connection.execute(
                "SELECT conversation_key, schema_version, context_version, ledger_cursor, "
                "ledger_version, observed_mode, context_json, state, checkpoint_cleanup_state, "
                "updated_at, tombstoned_at FROM conversation_contexts WHERE conversation_key = ?",
                (key,),
            ).fetchone()
            current_version = 0 if context is None else context[2]
            if context is not None and context[7] == "tombstoned":
                raise ConversationTombstonedError(key)
            existing = connection.execute(
                "SELECT conversation_key, turn_id, operation, base_context_version, state, "
                "selected_agent_id, route_source, result_json, delta_json, ledger_version, "
                "created_at, updated_at, expires_at FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if existing is not None:
                return BeginTurnResult(
                    False, current_version, self._turn(existing)
                )
            connection.execute(
                "INSERT INTO conversation_turns "
                "(conversation_key, turn_id, operation, base_context_version, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'in_progress', ?, ?)",
                (key, turn_id, operation, base_version, now, now),
            )
            row = connection.execute(
                "SELECT conversation_key, turn_id, operation, base_context_version, state, "
                "selected_agent_id, route_source, result_json, delta_json, ledger_version, "
                "created_at, updated_at, expires_at FROM conversation_turns WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
        logger.debug("conversation turn begun")
        return BeginTurnResult(True, current_version, self._turn(row))

    def stage_turn(
        self, key: str, turn_id: str, staged: StagedTurn
    ) -> StoredTurn:
        now = _now()
        expires = (
            datetime.fromisoformat(now)
            + timedelta(hours=ApiConfig().API_RUN_TTL_OK_HOURS)
        ).isoformat()
        result_json, delta_json = _json(staged.result), _pack_delta(staged)
        with self._write() as connection:
            row = connection.execute(
                "SELECT conversation_key, turn_id, operation, base_context_version, state, selected_agent_id, "
                "route_source, result_json, delta_json, ledger_version, created_at, updated_at, expires_at "
                "FROM conversation_turns WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                raise KeyError((key, turn_id))
            if row[4] in {"staged", "committed"}:
                if (
                    row[2] != staged.operation
                    or row[3] != staged.base_context_version
                    or row[5] != staged.selected_agent_id
                    or row[6] != staged.route_source
                    or row[7] != result_json
                    or row[8] != delta_json
                    or row[9] != staged.ledger_version
                ):
                    raise StagedTurnConflictError((key, turn_id))
                return self._turn(row)
            if row[4] != "in_progress":
                raise StagedTurnConflictError((key, turn_id))
            connection.execute(
                "UPDATE conversation_turns SET state='staged', selected_agent_id=?, route_source=?, "
                "result_json=?, delta_json=?, ledger_version=?, updated_at=?, expires_at=? "
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
            row = connection.execute(
                "SELECT conversation_key, turn_id, operation, base_context_version, state, selected_agent_id, route_source, "
                "result_json, delta_json, ledger_version, created_at, updated_at, expires_at FROM conversation_turns WHERE conversation_key=? AND turn_id=?",
                (key, turn_id),
            ).fetchone()
        logger.debug("conversation turn staged")
        return self._turn(row)

    def commit_staged_turn(
        self,
        key: str,
        turn_id: str,
        expected_ledger_version: str,
        ledger_version: str,
    ) -> SettlementResult:
        """Atomically apply a staged turn or return its existing commit."""
        now = _now()
        with self._write() as connection:
            turn = connection.execute(
                "SELECT conversation_key, turn_id, operation, base_context_version, state, selected_agent_id, route_source, result_json, delta_json, ledger_version, created_at, updated_at, expires_at FROM conversation_turns WHERE conversation_key=? AND turn_id=?",
                (key, turn_id),
            ).fetchone()
            if turn is None:
                raise KeyError((key, turn_id))
            if turn[4] not in {"staged", "committed"}:
                raise ContextVersionConflictError(key)
            if turn[9] != expected_ledger_version:
                raise ContextVersionConflictError(key)
            context = connection.execute(
                "SELECT conversation_key, schema_version, context_version, ledger_cursor, ledger_version, observed_mode, context_json, state, checkpoint_cleanup_state, updated_at, tombstoned_at FROM conversation_contexts WHERE conversation_key=?",
                (key,),
            ).fetchone()
            if context is not None and context[7] == "tombstoned":
                raise ConversationTombstonedError(key)
            if turn[4] == "committed":
                if context is None:
                    raise ContextVersionConflictError(key)
                return SettlementResult(
                    "already_applied", self._context(context)
                )
            current = 0 if context is None else context[2]
            if current != turn[3]:
                raise ContextVersionConflictError(key)
            data, metadata, _stage_metadata = _unpack_delta(turn[8])
            assert data is not None
            schema_version = metadata["schema_version"]
            cursor = metadata["ledger_cursor"]
            mode = metadata["observed_mode"]
            context_data = dict(data)
            if "last_applied_ledger_version" in context_data:
                context_data["last_applied_ledger_version"] = ledger_version
            context_json = _json(context_data)
            if context is None:
                connection.execute(
                    "INSERT INTO conversation_contexts VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'not_requested', ?, NULL)",
                    (
                        key,
                        schema_version,
                        1,
                        cursor,
                        ledger_version,
                        mode,
                        context_json,
                        now,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE conversation_contexts SET schema_version=?, context_version=?, ledger_cursor=?, ledger_version=?, observed_mode=?, context_json=?, state='active', updated_at=? WHERE conversation_key=? AND context_version=?",
                    (
                        schema_version,
                        current + 1,
                        cursor,
                        ledger_version,
                        mode,
                        context_json,
                        now,
                        key,
                        turn[3],
                    ),
                )
            connection.execute(
                "UPDATE conversation_turns SET state='committed', ledger_version=?, updated_at=? WHERE conversation_key=? AND turn_id=?",
                (ledger_version, now, key, turn_id),
            )
            row = connection.execute(
                "SELECT conversation_key, schema_version, context_version, ledger_cursor, ledger_version, observed_mode, context_json, state, checkpoint_cleanup_state, updated_at, tombstoned_at FROM conversation_contexts WHERE conversation_key=?",
                (key,),
            ).fetchone()
        logger.debug("conversation turn committed")
        return SettlementResult("committed", self._context(row))

    def mark_turn_failed(self, key: str, turn_id: str) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE conversation_turns SET state='failed', updated_at=? WHERE conversation_key=? AND turn_id=?",
                (_now(), key, turn_id),
            )

    def tombstone(self, key: str) -> None:
        now = _now()
        with self._write() as connection:
            connection.execute(
                "DELETE FROM conversation_turns WHERE conversation_key=?",
                (key,),
            )
            context = connection.execute(
                "SELECT context_version FROM conversation_contexts WHERE conversation_key=?",
                (key,),
            ).fetchone()
            if context is None:
                connection.execute(
                    "INSERT INTO conversation_contexts VALUES (?, 1, 0, 0, '', '', '{}', 'tombstoned', 'pending', ?, ?)",
                    (key, now, now),
                )
            else:
                connection.execute(
                    "UPDATE conversation_contexts SET context_json='{}', state='tombstoned', checkpoint_cleanup_state='pending', updated_at=?, tombstoned_at=? WHERE conversation_key=?",
                    (now, now, key),
                )

    def complete_checkpoint_cleanup(self, key: str) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE conversation_contexts SET checkpoint_cleanup_state='complete', updated_at=? WHERE conversation_key=? AND state='tombstoned'",
                (_now(), key),
            )

    def purge_expired_staged(self, now: str | datetime) -> int:
        now_value = now.isoformat() if isinstance(now, datetime) else now
        with self._write() as connection:
            cursor = connection.execute(
                "DELETE FROM conversation_turns WHERE state='staged' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now_value,),
            )
            return cursor.rowcount


__all__ = [
    "BeginTurnResult",
    "ConversationContextStore",
    "ConversationTombstonedError",
    "ContextVersionConflictError",
    "SettlementResult",
    "StagedTurn",
    "StagedTurnConflictError",
    "StoredBusinessContext",
    "StoredTurn",
]
