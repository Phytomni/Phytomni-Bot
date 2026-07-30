# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Durable Bot-owned conversation context and terminal-turn staging."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self
from uuid import UUID, uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX.
    fcntl = None  # type: ignore[assignment]

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


class ContextVersionConflictError(RuntimeError):
    """Raised when a turn's base context version is stale."""


class ConversationTombstonedError(RuntimeError):
    """Raised when work is attempted for a deleted conversation."""


class StagedTurnConflictError(RuntimeError):
    """Raised when a retry proposes different terminal bytes."""


class ReviewMutationLockTimeoutError(TimeoutError):
    """Raised when a cross-worker Review mutation lock cannot be acquired."""


class ReviewMutationLock:
    """Releasable lock held across a private checkpoint write."""

    def __init__(
        self,
        *,
        file_descriptor: int | None = None,
        local_lock: threading.Lock | None = None,
    ) -> None:
        self._file_descriptor = file_descriptor
        self._local_lock = local_lock
        self._state_lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        """Release the lock exactly once; release may run in another thread."""
        with self._state_lock:
            if self._released:
                return
            self._released = True
            file_descriptor = self._file_descriptor
            local_lock = self._local_lock
            self._file_descriptor = None
            self._local_lock = None
        if file_descriptor is not None:
            if fcntl is not None:
                fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            os.close(file_descriptor)
        elif local_lock is not None:
            local_lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


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
class _ReviewClaimLookupRequest:
    """Inputs for precondition checks before a Review marker claim."""

    connection: sqlite3.Connection
    key: str
    row: sqlite3.Row | tuple[Any, ...]
    expected_ledger_version: str | None
    expected_base_context_version: int | None


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


_REVIEW_SETTLEMENT_CLAIM_TTL = timedelta(minutes=5)
_REVIEW_SETTLEMENT_TOKEN_LIMIT = 64
_REVIEW_SETTLEMENT_TIMESTAMP_LIMIT = 64
_REVIEW_SETTLEMENT_FENCE_LIMIT = 2**63 - 1
_REVIEW_THREAD_ID_LIMIT = 512

_REVIEW_MUTATION_LOCKS: dict[str, threading.Lock] = {}
_REVIEW_MUTATION_LOCKS_GUARD = threading.Lock()


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


class ConversationContextStore:
    """Persist versioned context and one terminal proposal per turn."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or resolve_tasks_db_path()
        self._init_db()

    def acquire_review_mutation_lock(
        self, *, timeout: float | None = 30.0
    ) -> ReviewMutationLock:
        """Serialize checkpoint mutation and tombstone cleanup across workers.

        SQLite transactions protect durable rows, but Review promotion also
        writes an external checkpoint.  This lock spans both mutations so a
        tombstone cannot interleave between the promotion fence check and the
        checkpoint write.
        """
        path = os.fspath(self.db_path)
        if fcntl is None or path == ":memory:":
            with _REVIEW_MUTATION_LOCKS_GUARD:
                lock = _REVIEW_MUTATION_LOCKS.setdefault(
                    path, threading.Lock()
                )
            acquired = lock.acquire(timeout=-1 if timeout is None else timeout)
            if not acquired:
                raise ReviewMutationLockTimeoutError(path)
            return ReviewMutationLock(local_lock=lock)

        file_descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return ReviewMutationLock(file_descriptor=file_descriptor)
                except (BlockingIOError, OSError) as exc:
                    if not isinstance(exc, BlockingIOError) and getattr(
                        exc, "errno", None
                    ) not in {11, 13}:
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ReviewMutationLockTimeoutError(path) from exc
                    time.sleep(0.01)
        except BaseException:
            os.close(file_descriptor)
            raise

    def _init_db(self) -> None:
        with sqlite_connection(self.db_path) as connection:
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
    def write(self):
        with sqlite_connection(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    @contextmanager
    def _write(self):
        """Compatibility alias for the public transaction context."""
        with self.write() as connection:
            yield connection

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
                "ledger_cursor, ledger_version, observed_mode, context_json, "
                "state, "
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
                return BeginTurnResult(
                    False, current_version, self._turn(existing)
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
        return BeginTurnResult(True, current_version, self._turn(row))

    @staticmethod
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
        self, entry: _ReviewCleanupEntry
    ) -> bool:
        """Persist a validated candidate while the mutation lock is held."""
        with self._write() as connection:
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
                or (
                    existing[2] is not None
                    and existing[2] != entry.operation
                )
            ):
                return False
            self._upsert_review_checkpoint_cleanup(connection, entry)
        return True

    def register_review_candidate(
        self,
        key: str,
        turn_id: str,
        operation: str,
        stable_thread_id: str,
        candidate_thread_id: str,
        *,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Register a candidate before Review can write its checkpoint.

        Registration is intentionally separate from turn staging.  A graph
        may create a checkpoint before the public result is stageable, so the
        cleanup identity must already be durable at the graph boundary.
        """
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
        if operation not in {
            "new_review",
            "scope_change",
        } or not self._marker_is_bounded(marker, key=key, turn_id=turn_id):
            return False
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.register_review_candidate(
                    key,
                    turn_id,
                    operation,
                    stable_thread_id,
                    candidate_thread_id,
                    mutation_lock_held=True,
                )
        return self._register_review_candidate_locked(
            _ReviewCleanupEntry(
                key=key,
                candidate=candidate_thread_id,
                turn_id=turn_id,
                operation=operation,
                now=_now(),
                staged=False,
            )
        )

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
                marker = staged.stage_metadata.get("_review_settlement")
                if isinstance(marker, Mapping):
                    candidate = _review_candidate_thread_id(marker)
                    operation = marker.get("operation")
                    marker_turn_id = marker.get("turn_id")
                    if (
                        candidate is not None
                        and isinstance(operation, str)
                        and isinstance(marker_turn_id, str)
                    ):
                        self._upsert_review_checkpoint_cleanup(
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
                return self._turn(row)
            if row[4] != "in_progress":
                raise StagedTurnConflictError((key, turn_id))
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
            marker = staged.stage_metadata.get("_review_settlement")
            if isinstance(marker, Mapping):
                candidate = _review_candidate_thread_id(marker)
                operation = marker.get("operation")
                marker_turn_id = marker.get("turn_id")
                if (
                    candidate is not None
                    and isinstance(operation, str)
                    and isinstance(marker_turn_id, str)
                ):
                    self._upsert_review_checkpoint_cleanup(
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
            row = connection.execute(
                "SELECT conversation_key, turn_id, operation, "
                "base_context_version, state, selected_agent_id, "
                "route_source, result_json, delta_json, ledger_version, "
                "created_at, updated_at, expires_at FROM conversation_turns "
                "WHERE conversation_key=? AND turn_id=?",
                (key, turn_id),
            ).fetchone()
        logger.debug("conversation turn staged")
        return self._turn(row)

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _claim_datetime(value: datetime | str | None) -> datetime:
        """Normalize a testable claim clock to UTC."""
        if value is None:
            return datetime.now(UTC)
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _write_review_marker(
        cls,
        connection: sqlite3.Connection,
        key: str,
        turn_id: str,
        decoded: dict[str, Any],
        marker: Mapping[str, Any],
        now: str,
    ) -> bool:
        delta_json = cls._with_review_record(decoded, marker)
        if delta_json is None:
            return False
        connection.execute(
            "UPDATE conversation_turns SET delta_json = ?, updated_at = ? "
            "WHERE conversation_key = ? AND turn_id = ?",
            (delta_json, now, key, turn_id),
        )
        return True

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _marker_stable_thread_id(marker: Mapping[str, Any]) -> str | None:
        """Return a bounded stable Review thread identity."""
        return _bounded_stable_thread_id(marker.get("stable_thread_id"))

    @staticmethod
    def _turn_id_is_bounded(turn_id: str) -> bool:
        """Reject turn identifiers that could escape the bounded marker."""
        return _bounded_review_id(turn_id, max_length=64) is not None

    @staticmethod
    def _report_revision_is_bounded(marker: Mapping[str, Any]) -> bool:
        """Reject malformed Review report revisions."""
        report_revision = marker.get("report_revision")
        return (
            report_revision is not None
            and not isinstance(report_revision, bool)
            and isinstance(report_revision, int)
            and report_revision >= 0
        )

    @classmethod
    def _bounded_marker_fields(
        cls, marker: Mapping[str, Any], turn_id: str
    ) -> tuple[str, str] | None:
        """Validate marker fields shared by every Review settlement state."""
        operation = cls._marker_operation(marker)
        stable = cls._marker_stable_thread_id(marker)
        if operation is None or stable is None:
            return None
        if marker.get("turn_id") != turn_id:
            return None
        if not cls._turn_id_is_bounded(turn_id):
            return None
        if not cls._report_revision_is_bounded(marker):
            return None
        return operation, stable

    @staticmethod
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

    @staticmethod
    def _marker_candidate_is_bounded(
        marker: Mapping[str, Any], operation: str
    ) -> bool:
        """Validate candidate identity only for candidate-producing states."""
        candidate = marker.get("candidate_thread_id")
        if operation in {"new_review", "scope_change"}:
            return _review_candidate_thread_id(marker) is not None
        return candidate is None

    @classmethod
    def _marker_is_bounded(
        cls, marker: Mapping[str, Any], *, key: str, turn_id: str
    ) -> bool:
        """Reject marker identities that cannot belong to this staged row."""
        fields = cls._bounded_marker_fields(marker, turn_id)
        if fields is None:
            return False
        operation, stable = fields
        return cls._stable_marker_matches_key(
            stable, key
        ) and cls._marker_candidate_is_bounded(marker, operation)

    @staticmethod
    def _claim_is_expired(
        claimed_at: str, *, clock: datetime, stale_after: timedelta
    ) -> bool:
        try:
            claimed_clock = ConversationContextStore._claim_datetime(
                claimed_at
            )
        except (TypeError, ValueError):
            return True
        return clock - claimed_clock >= stale_after

    def _claim_row_failure(
        self, request: _ReviewClaimLookupRequest
    ) -> ReviewSettlementClaim | None:
        """Return the first durable row precondition failure, if any."""
        context = request.connection.execute(
            "SELECT context_version, state FROM conversation_contexts "
            "WHERE conversation_key = ?",
            (request.key,),
        ).fetchone()
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

    @staticmethod
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

    def _claim_active_marker(
        self,
        request: _ReviewClaimMarkerRequest,
        state: ReviewSettlementClaimStatus,
    ) -> ReviewSettlementClaim:
        """Refresh an expired settling/promoting marker claim."""
        parts = self._bounded_claim_parts(
            request.marker.get("settlement_claim_token"),
            request.marker.get("settlement_claimed_at"),
            self._marker_fence(request.marker),
        )
        if parts is None:
            return ReviewSettlementClaim("invalid")
        token, claimed_at, fence = parts
        if not self._claim_is_expired(
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
        if not self._write_review_marker(
            request.connection,
            request.identity.key,
            request.identity.turn_id,
            request.decoded,
            updated,
            request.timing.now_value,
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
        if not self._write_review_marker(
            request.connection,
            request.identity.key,
            request.identity.turn_id,
            request.decoded,
            updated,
            request.timing.now_value,
        ):
            return ReviewSettlementClaim("invalid")
        return ReviewSettlementClaim("claimed", claim_token, fence)

    def _claim_review_marker(
        self, request: _ReviewClaimMarkerRequest
    ) -> ReviewSettlementClaim:
        """Advance a validated Review marker under its open transaction."""
        state = request.marker.get("settlement_state")
        if state not in {
            "pending",
            "settling",
            "promoting",
            "promoted",
            "rejected",
            "failed",
        }:
            return ReviewSettlementClaim("invalid")
        if state in {"promoted", "rejected", "failed"}:
            return ReviewSettlementClaim(state)
        if state in {"settling", "promoting"}:
            return self._claim_active_marker(request, state)
        return self._claim_pending_marker(request)

    def claim_review_settlement(
        self,
        key: str,
        turn_id: str,
        *,
        now: datetime | str | None = None,
        stale_after: timedelta = _REVIEW_SETTLEMENT_CLAIM_TTL,
        expected_ledger_version: str | None = None,
        expected_base_context_version: int | None = None,
    ) -> ReviewSettlementClaim:
        """Claim a staged Review marker with a durable compare-and-set."""
        clock = self._claim_datetime(now)
        now_value = clock.isoformat()
        with self._write() as connection:
            row = connection.execute(
                "SELECT state, ledger_version, base_context_version, "
                "delta_json "
                "FROM conversation_turns "
                "WHERE conversation_key = ? "
                "AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                return ReviewSettlementClaim("missing")
            failure = self._claim_row_failure(
                _ReviewClaimLookupRequest(
                    connection=connection,
                    key=key,
                    row=row,
                    expected_ledger_version=expected_ledger_version,
                    expected_base_context_version=expected_base_context_version,
                )
            )
            if failure is not None:
                return failure
            record = self._review_record(row[3])
            if record is None:
                return ReviewSettlementClaim("invalid")
            decoded, marker = record
            if not self._marker_is_bounded(marker, key=key, turn_id=turn_id):
                return ReviewSettlementClaim("invalid")
            return self._claim_review_marker(
                _ReviewClaimMarkerRequest(
                    connection=connection,
                    identity=_ReviewClaimIdentity(key=key, turn_id=turn_id),
                    row=row,
                    decoded=decoded,
                    marker=marker,
                    timing=_ReviewClaimTiming(
                        now_value=now_value,
                        clock=clock,
                        stale_after=stale_after,
                    ),
                )
            )

    def reserve_review_settlement(
        self,
        key: str,
        turn_id: str,
        *,
        claim_token: str,
        fence_token: int,
        expected_ledger_version: str | None = None,
        expected_base_context_version: int | None = None,
    ) -> ReviewSettlementClaim:
        """Reserve the staged proposal before a private checkpoint write."""
        if (
            not isinstance(claim_token, str)
            or not claim_token
            or len(claim_token) > _REVIEW_SETTLEMENT_TOKEN_LIMIT
            or isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
            or fence_token > _REVIEW_SETTLEMENT_FENCE_LIMIT
        ):
            return ReviewSettlementClaim("invalid")
        with self._write() as connection:
            row = connection.execute(
                "SELECT state, ledger_version, base_context_version, "
                "delta_json "
                "FROM conversation_turns WHERE conversation_key = ? "
                "AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                return ReviewSettlementClaim("missing")
            context = connection.execute(
                "SELECT context_version, state FROM conversation_contexts "
                "WHERE conversation_key = ?",
                (key,),
            ).fetchone()
            if context is not None and context[1] == "tombstoned":
                return ReviewSettlementClaim("conflict")
            if row[0] != "staged":
                if row[0] == "committed":
                    record = self._review_record(row[3])
                    if (
                        record is not None
                        and record[1].get("settlement_state") == "promoted"
                    ):
                        return ReviewSettlementClaim("promoted")
                return ReviewSettlementClaim("conflict")
            if (
                expected_ledger_version is not None
                and row[1] != expected_ledger_version
            ) or (
                expected_base_context_version is not None
                and row[2] != expected_base_context_version
            ):
                return ReviewSettlementClaim("conflict")
            current_version = 0 if context is None else context[0]
            if current_version != row[2]:
                return ReviewSettlementClaim("conflict")
            record = self._review_record(row[3])
            if record is None:
                return ReviewSettlementClaim("invalid")
            decoded, marker = record
            if not self._marker_is_bounded(marker, key=key, turn_id=turn_id):
                return ReviewSettlementClaim("invalid")
            state = marker.get("settlement_state")
            if state == "promoting":
                if (
                    marker.get("settlement_claim_token") == claim_token
                    and self._marker_fence(marker) == fence_token
                ):
                    return ReviewSettlementClaim(
                        "promoting", claim_token, fence_token
                    )
                return ReviewSettlementClaim("conflict")
            if state != "settling":
                if state in {"promoted", "rejected", "failed"}:
                    return ReviewSettlementClaim(state)
                return ReviewSettlementClaim("conflict")
            if (
                marker.get("settlement_claim_token") != claim_token
                or self._marker_fence(marker) != fence_token
                or marker.get("settlement_ledger_version") != row[1]
                or marker.get("settlement_base_context_version") != row[2]
            ):
                return ReviewSettlementClaim("conflict")
            updated = dict(marker)
            updated["settlement_state"] = "promoting"
            if not self._write_review_marker(
                connection, key, turn_id, decoded, updated, _now()
            ):
                return ReviewSettlementClaim("invalid")
            return ReviewSettlementClaim("promoting", claim_token, fence_token)

    def is_review_settlement_claim_active(
        self,
        key: str,
        turn_id: str,
        *,
        claim_token: str,
        fence_token: int | None = None,
    ) -> bool:
        """Check a Review fencing token immediately before private writes."""
        if (
            not isinstance(claim_token, str)
            or not claim_token
            or len(claim_token) > _REVIEW_SETTLEMENT_TOKEN_LIMIT
        ):
            return False
        if fence_token is not None and (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
            or fence_token > _REVIEW_SETTLEMENT_FENCE_LIMIT
        ):
            return False
        with sqlite_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT delta_json FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
        if row is None:
            return False
        record = self._review_record(row[0])
        if record is None:
            return False
        _decoded, marker = record
        return (
            marker.get("settlement_state") in {"settling", "promoting"}
            and marker.get("settlement_claim_token") == claim_token
            and (
                fence_token is None
                or marker.get("settlement_fence") == fence_token
            )
        )

    def finalize_review_settlement(
        self,
        key: str,
        turn_id: str,
        *,
        claim_token: str,
        state: Literal["promoted", "rejected", "failed"],
        report_revision: int | None = None,
        fence_token: int | None = None,
    ) -> bool:
        """Finalize only the worker that durably claimed a Review marker."""
        if (
            not isinstance(claim_token, str)
            or not claim_token
            or len(claim_token) > _REVIEW_SETTLEMENT_TOKEN_LIMIT
        ):
            return False
        if fence_token is not None and (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
            or fence_token > _REVIEW_SETTLEMENT_FENCE_LIMIT
        ):
            return False
        if state == "promoted" and (
            report_revision is None
            or isinstance(report_revision, bool)
            or report_revision < 0
        ):
            return False
        with self._write() as connection:
            row = connection.execute(
                "SELECT delta_json FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                return False
            record = self._review_record(row[0])
            if record is None:
                return False
            decoded, marker = record
            current_state = marker.get("settlement_state")
            if current_state == state:
                return True
            if current_state in {"promoted", "rejected", "failed"}:
                return current_state == state
            if (
                current_state not in {"settling", "promoting"}
                or marker.get("settlement_claim_token") != claim_token
                or (
                    fence_token is not None
                    and marker.get("settlement_fence") != fence_token
                )
            ):
                return False
            updated = dict(marker)
            updated["settlement_state"] = state
            updated.pop("settlement_claim_token", None)
            updated.pop("settlement_claimed_at", None)
            if report_revision is not None:
                updated["report_revision"] = report_revision
            return self._write_review_marker(
                connection, key, turn_id, decoded, updated, _now()
            )

    def mark_review_settlement_failed(
        self,
        key: str,
        turn_id: str,
        *,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Persist a terminal failure for a malformed or abandoned marker."""
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.mark_review_settlement_failed(
                    key, turn_id, mutation_lock_held=True
                )
        with self._write() as connection:
            row = connection.execute(
                "SELECT delta_json FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                return False
            record = self._review_record(row[0])
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
            return self._write_review_marker(
                connection, key, turn_id, decoded, marker, _now()
            )

    def update_review_settlement_metadata(
        self,
        key: str,
        turn_id: str,
        updates: Mapping[str, Any],
    ) -> bool:
        """Update private Review metadata for compatibility test seams."""
        with self._write() as connection:
            row = connection.execute(
                "SELECT delta_json FROM conversation_turns "
                "WHERE conversation_key = ? AND turn_id = ?",
                (key, turn_id),
            ).fetchone()
            if row is None:
                return False
            record = self._review_record(row[0])
            if record is None:
                return False
            decoded, marker = record
            marker.update(dict(updates))
            return self._write_review_marker(
                connection, key, turn_id, decoded, marker, _now()
            )

    def commit_staged_turn(
        self,
        key: str,
        turn_id: str,
        expected_ledger_version: str,
        ledger_version: str,
        *,
        mutation_lock_held: bool = False,
    ) -> SettlementResult:
        """Atomically apply a staged turn or return its existing commit."""
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.commit_staged_turn(
                    key,
                    turn_id,
                    expected_ledger_version,
                    ledger_version,
                    mutation_lock_held=True,
                )
        now = _now()
        with self._write() as connection:
            turn = connection.execute(
                "SELECT conversation_key, turn_id, operation, "
                "base_context_version, state, selected_agent_id, "
                "route_source, result_json, delta_json, ledger_version, "
                "created_at, updated_at, expires_at "
                "FROM conversation_turns WHERE conversation_key=? "
                "AND turn_id=?",
                (key, turn_id),
            ).fetchone()
            if turn is None:
                raise KeyError((key, turn_id))
            if turn[4] not in {"staged", "committed"}:
                raise ContextVersionConflictError(key)
            if turn[9] != expected_ledger_version:
                raise ContextVersionConflictError(key)
            review_record = self._review_record(turn[8])
            if review_record is not None:
                _decoded_review, review_marker = review_record
                if (
                    review_marker.get("settlement_state") != "promoted"
                    or review_marker.get("settlement_ledger_version")
                    != expected_ledger_version
                    or review_marker.get("settlement_base_context_version")
                    != turn[3]
                    or self._marker_fence(review_marker) is None
                ):
                    raise ContextVersionConflictError(key)
            context = connection.execute(
                "SELECT conversation_key, schema_version, context_version, "
                "ledger_cursor, ledger_version, observed_mode, context_json, "
                "state, checkpoint_cleanup_state, updated_at, tombstoned_at "
                "FROM conversation_contexts WHERE conversation_key=?",
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
                    "INSERT INTO conversation_contexts VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, 'active', 'not_requested', ?, "
                    "NULL)",
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
                    "UPDATE conversation_contexts SET schema_version=?, "
                    "context_version=?, ledger_cursor=?, ledger_version=?, "
                    "observed_mode=?, context_json=?, state='active', "
                    "updated_at=? WHERE conversation_key=? "
                    "AND context_version=?",
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
                "UPDATE conversation_turns SET state='committed', "
                "ledger_version=?, updated_at=? WHERE conversation_key=? "
                "AND turn_id=?",
                (ledger_version, now, key, turn_id),
            )
            row = connection.execute(
                "SELECT conversation_key, schema_version, context_version, "
                "ledger_cursor, ledger_version, observed_mode, context_json, "
                "state, checkpoint_cleanup_state, updated_at, tombstoned_at "
                "FROM conversation_contexts WHERE conversation_key=?",
                (key,),
            ).fetchone()
        logger.debug("conversation turn committed")
        return SettlementResult("committed", self._context(row))

    def mark_turn_failed(self, key: str, turn_id: str) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE conversation_turns SET state='failed', "
                "updated_at=? WHERE conversation_key=? AND turn_id=?",
                (_now(), key, turn_id),
            )

    def tombstone(
        self, key: str, *, mutation_lock_held: bool = False
    ) -> tuple[str, ...]:
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.tombstone(key, mutation_lock_held=True)
        now = _now()
        with self._write() as connection:
            candidates = {
                row[0]
                for row in connection.execute(
                    "SELECT candidate_thread_id "
                    "FROM conversation_review_checkpoint_cleanup "
                    "WHERE conversation_key = ?",
                    (key,),
                ).fetchall()
            }
            turn_rows = connection.execute(
                "SELECT turn_id, delta_json FROM conversation_turns "
                "WHERE conversation_key = ?",
                (key,),
            ).fetchall()
            for turn_id, delta_json in turn_rows:
                record = self._review_record(delta_json)
                if record is None:
                    continue
                decoded, marker = record
                candidate = _review_candidate_thread_id(marker)
                if candidate is not None:
                    candidates.add(candidate)
                    operation = marker.get("operation")
                    if not isinstance(operation, str):
                        operation = "new_review"
                    self._upsert_review_checkpoint_cleanup(
                        connection,
                        _ReviewCleanupEntry(
                            key=key,
                            candidate=candidate,
                            turn_id=turn_id,
                            operation=operation,
                            now=now,
                            staged=True,
                        ),
                    )
                if marker.get("settlement_state") in {
                    "pending",
                    "settling",
                    "promoting",
                }:
                    failed_marker = dict(marker)
                    failed_marker["settlement_state"] = "failed"
                    failed_marker.pop("settlement_claim_token", None)
                    failed_marker.pop("settlement_claimed_at", None)
                    self._write_review_marker(
                        connection,
                        key,
                        turn_id,
                        decoded,
                        failed_marker,
                        now,
                    )
            connection.execute(
                "UPDATE conversation_review_checkpoint_cleanup SET "
                "tombstone_pending = 1 WHERE conversation_key = ?",
                (key,),
            )
            connection.execute(
                "DELETE FROM conversation_turns WHERE conversation_key=?",
                (key,),
            )
            context = connection.execute(
                "SELECT context_version FROM conversation_contexts "
                "WHERE conversation_key=?",
                (key,),
            ).fetchone()
            if context is None:
                connection.execute(
                    "INSERT INTO conversation_contexts VALUES "
                    "(?, 1, 0, 0, '', '', '{}', 'tombstoned', "
                    "'pending', ?, ?)",
                    (key, now, now),
                )
            else:
                connection.execute(
                    "UPDATE conversation_contexts SET context_json='{}', "
                    "state='tombstoned', checkpoint_cleanup_state='pending', "
                    "updated_at=?, tombstoned_at=? WHERE conversation_key=?",
                    (now, now, key),
                )
        return tuple(sorted(candidates))

    def complete_checkpoint_cleanup(
        self, key: str, *, mutation_lock_held: bool = False
    ) -> None:
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                self.complete_checkpoint_cleanup(key, mutation_lock_held=True)
                return
        with self._write() as connection:
            updated = connection.execute(
                "UPDATE conversation_contexts SET "
                "checkpoint_cleanup_state='complete', updated_at=? "
                "WHERE conversation_key=? AND state='tombstoned'",
                (_now(), key),
            )
            if updated.rowcount:
                connection.execute(
                    "DELETE FROM conversation_review_checkpoint_cleanup "
                    "WHERE conversation_key = ? AND staged_at IS NOT NULL",
                    (key,),
                )

    def purge_expired_staged(
        self,
        now: str | datetime,
        *,
        mutation_lock_held: bool = False,
    ) -> int:
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.purge_expired_staged(now, mutation_lock_held=True)
        return self._purge_expired_staged(now)

    def _purge_expired_staged(self, now: str | datetime) -> int:
        now_value = now.isoformat() if isinstance(now, datetime) else now
        with self._write() as connection:
            rows = connection.execute(
                "SELECT conversation_key, turn_id, delta_json "
                "FROM conversation_turns "
                "WHERE state='staged' AND expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (now_value,),
            ).fetchall()
            for key, turn_id, delta_json in rows:
                record = self._review_record(delta_json)
                if record is None:
                    continue
                _decoded, marker = record
                candidate = _review_candidate_thread_id(marker)
                if candidate is not None:
                    operation = marker.get("operation")
                    if not isinstance(operation, str):
                        operation = "new_review"
                    self._upsert_review_checkpoint_cleanup(
                        connection,
                        _ReviewCleanupEntry(
                            key=key,
                            candidate=candidate,
                            turn_id=turn_id,
                            operation=operation,
                            now=now_value,
                            staged=True,
                        ),
                    )
                    connection.execute(
                        "UPDATE conversation_review_checkpoint_cleanup "
                        "SET eligible_at = COALESCE(eligible_at, ?) "
                        "WHERE conversation_key = ? "
                        "AND candidate_thread_id = ?",
                        (now_value, key, candidate),
                    )
            cursor = connection.execute(
                "DELETE FROM conversation_turns WHERE state='staged' "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (now_value,),
            )
            return cursor.rowcount

    def list_checkpoint_cleanup_candidates(
        self, *, mutation_lock_held: bool = False
    ) -> tuple[tuple[str, str], ...]:
        """Return candidate cleanup rows that remain retryable."""
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.list_checkpoint_cleanup_candidates(
                    mutation_lock_held=True
                )
        with sqlite_connection(self.db_path) as connection:
            rows = connection.execute(
                "SELECT conversation_key, candidate_thread_id "
                "FROM conversation_review_checkpoint_cleanup "
                "WHERE eligible_at IS NOT NULL OR tombstone_pending = 1 "
                "ORDER BY conversation_key, candidate_thread_id"
            ).fetchall()
        return tuple((row[0], row[1]) for row in rows)

    def complete_checkpoint_cleanup_candidates(
        self,
        candidates: Sequence[tuple[str, str]],
        *,
        mutation_lock_held: bool = False,
    ) -> None:
        """Remove only candidate rows whose checkpoint deletion succeeded."""
        if not candidates:
            return
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                self.complete_checkpoint_cleanup_candidates(
                    candidates, mutation_lock_held=True
                )
                return
        with self._write() as connection:
            connection.executemany(
                "DELETE FROM conversation_review_checkpoint_cleanup "
                "WHERE conversation_key = ? AND candidate_thread_id = ?",
                candidates,
            )


__all__ = [
    "BeginTurnResult",
    "ContextVersionConflictError",
    "ConversationContextStore",
    "ConversationTombstonedError",
    "ReviewMutationLock",
    "ReviewMutationLockTimeoutError",
    "ReviewSettlementClaim",
    "SettlementResult",
    "StagedTurn",
    "StagedTurnConflictError",
    "StoredBusinessContext",
    "StoredTurn",
]
