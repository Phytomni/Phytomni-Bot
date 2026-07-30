# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Durable Bot-owned conversation context and terminal-turn staging."""

from __future__ import annotations

import inspect
import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from ..sqlite import sqlite_connection
from ..task_manager import resolve_tasks_db_path
from .review_claim import (
    _claim_active_marker,
    _claim_pending_marker,
    _claim_review_marker,
    _claim_review_settlement,
    _ReviewClaimRequest,
)
from .review_finalize import (
    _finalize_review_settlement,
    _finalize_review_settlement_locked,
    _mark_review_settlement_failed,
    _ReviewFailureCall,
    _ReviewFinalizeCall,
    _ReviewMetadataCall,
    _update_review_settlement_metadata,
)
from .review_lock import (
    ReviewMutationLock,
    ReviewMutationLockTimeoutError,
    acquire_review_mutation_lock,
)
from .review_mixin import (
    _bounded_claim_parts,
    _bounded_marker_fields,
    _claim_datetime,
    _claim_is_expired,
    _claim_row_failure,
    _marker_candidate_is_bounded,
    _marker_fence,
    _marker_is_bounded,
    _marker_operation,
    _marker_stable_thread_id,
    _report_revision_is_bounded,
    _review_context_state,
    _review_record,
    _stable_marker_matches_key,
    _turn_id_is_bounded,
    _with_review_record,
    _write_review_marker,
)
from .review_reservation import (
    _reserve_marker_state,
    _reserve_review_settlement,
    _reserve_row_failure,
    _review_reservation_inputs_valid,
    _ReviewReservationRequest,
)
from .review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    ReviewSettlementClaim,
    _now,
    _review_candidate_thread_id,
    _ReviewCleanupEntry,
    _ReviewMarkerWriteRequest,
)
from .store_core import (
    _context,
    _init_db,
    _register_review_candidate_locked,
    _RegisterCandidateCall,
    _staged_turn_matches,
    _turn,
    _upsert_review_checkpoint_cleanup,
    _write,
    begin_turn,
    configure_store_types,
    load_context,
    load_turn,
    register_review_candidate,
    stage_turn,
    write,
)
from .store_core import (
    _pack_delta as _core_pack_delta,
)
from .store_facade import install_store_facades
from .turn_commit import (
    ContextVersionConflictError,
    ConversationTombstonedError,
    SettlementResult,
    _apply_staged_turn_locked,
    _commit_staged_turn,
    _CommitCall,
)

_COMMIT_STAGED_TURN_SIGNATURE = inspect.Signature(
    parameters=(
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("key", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("turn_id", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter(
            "expected_ledger_version", inspect.Parameter.POSITIONAL_OR_KEYWORD
        ),
        inspect.Parameter(
            "ledger_version", inspect.Parameter.POSITIONAL_OR_KEYWORD
        ),
        inspect.Parameter(
            "mutation_lock_held",
            inspect.Parameter.KEYWORD_ONLY,
            default=False,
        ),
    )
)

# Preserve the historical public import and pickle path after support split.
ContextVersionConflictError.__module__ = __name__
ConversationTombstonedError.__module__ = __name__
SettlementResult.__module__ = __name__
ReviewMutationLock.__module__ = __name__
ReviewMutationLockTimeoutError.__module__ = __name__
ReviewSettlementClaim.__module__ = __name__

logger = logging.getLogger(__name__)


class StagedTurnConflictError(RuntimeError):
    """Raised when a retry proposes different terminal bytes."""


@dataclass(frozen=True)
class _StoredBusinessContextBase:
    """Stable leading fields for the durable context value object."""

    conversation_key: str
    schema_version: int
    context_version: int
    ledger_cursor: int
    ledger_version: str


@dataclass(frozen=True)
class StoredBusinessContext(_StoredBusinessContextBase):
    """Durable versioned business context for one conversation."""

    observed_mode: str
    context: dict[str, Any]
    state: Literal["active", "tombstoned"]
    checkpoint_cleanup_state: Literal["not_requested", "pending", "complete"]
    updated_at: str
    tombstoned_at: str | None


@dataclass(frozen=True)
class _StagedTurnBase:
    """Stable leading fields for a staged terminal proposal."""

    operation: str
    base_context_version: int
    selected_agent_id: str
    route_source: str
    result: dict[str, Any]


@dataclass(frozen=True)
class StagedTurn(_StagedTurnBase):
    """Terminal turn proposal persisted before an atomic commit."""

    delta: dict[str, Any]
    ledger_version: str
    schema_version: int
    ledger_cursor: int
    observed_mode: str
    stage_metadata: dict[str, Any]


@dataclass(frozen=True)
class _StoredTurnBase:
    """Stable leading fields for a durable turn row."""

    conversation_key: str
    turn_id: str
    operation: str
    base_context_version: int
    state: Literal["in_progress", "staged", "committed", "failed"]
    selected_agent_id: str | None
    route_source: str | None


@dataclass(frozen=True)
class StoredTurn(_StoredTurnBase):
    """Durable turn row returned by context-store reads."""

    result: dict[str, Any] | None
    delta: dict[str, Any] | None
    stage_metadata: dict[str, Any] | None
    ledger_version: str | None
    created_at: str
    updated_at: str
    expires_at: str | None


@dataclass(frozen=True)
class BeginTurnResult:
    """Result of creating or reusing an in-progress turn row."""

    created: bool
    context_version: int
    turn: StoredTurn


StoredBusinessContext.__annotations__ = {
    "conversation_key": str,
    "schema_version": int,
    "context_version": int,
    "ledger_cursor": int,
    "ledger_version": str,
    "observed_mode": str,
    "context": dict[str, Any],
    "state": Literal["active", "tombstoned"],
    "checkpoint_cleanup_state": Literal[
        "not_requested", "pending", "complete"
    ],
    "updated_at": str,
    "tombstoned_at": str | None,
}
StagedTurn.__annotations__ = {
    "operation": str,
    "base_context_version": int,
    "selected_agent_id": str,
    "route_source": str,
    "result": dict[str, Any],
    "delta": dict[str, Any],
    "ledger_version": str,
    "schema_version": int,
    "ledger_cursor": int,
    "observed_mode": str,
    "stage_metadata": dict[str, Any],
}
StoredTurn.__annotations__ = {
    "conversation_key": str,
    "turn_id": str,
    "operation": str,
    "base_context_version": int,
    "state": Literal["in_progress", "staged", "committed", "failed"],
    "selected_agent_id": str | None,
    "route_source": str | None,
    "result": dict[str, Any] | None,
    "delta": dict[str, Any] | None,
    "stage_metadata": dict[str, Any] | None,
    "ledger_version": str | None,
    "created_at": str,
    "updated_at": str,
    "expires_at": str | None,
}


def _pack_delta(staged: StagedTurn) -> str:
    """Preserve the historical private delta-packing import seam."""
    return _core_pack_delta(staged)


configure_store_types(
    context_type=StoredBusinessContext,
    turn_type=StoredTurn,
    begin_turn_type=BeginTurnResult,
    conflict_type=StagedTurnConflictError,
)


class ConversationContextStore:
    """Persist versioned context and one terminal proposal per turn."""

    register_review_candidate: Callable[..., bool]
    claim_review_settlement: Callable[..., ReviewSettlementClaim]
    reserve_review_settlement: Callable[..., ReviewSettlementClaim]
    finalize_review_settlement: Callable[..., bool]

    if TYPE_CHECKING:
        _review_context_state = staticmethod(_review_context_state)
        _review_record = staticmethod(_review_record)
        _with_review_record = staticmethod(_with_review_record)
        _claim_datetime = staticmethod(_claim_datetime)
        _write_review_marker = classmethod(_write_review_marker)
        _marker_fence = staticmethod(_marker_fence)
        _marker_operation = staticmethod(_marker_operation)
        _marker_stable_thread_id = staticmethod(_marker_stable_thread_id)
        _turn_id_is_bounded = staticmethod(_turn_id_is_bounded)
        _report_revision_is_bounded = staticmethod(_report_revision_is_bounded)
        _bounded_marker_fields = classmethod(_bounded_marker_fields)
        _stable_marker_matches_key = staticmethod(_stable_marker_matches_key)
        _marker_candidate_is_bounded = staticmethod(
            _marker_candidate_is_bounded
        )
        _marker_is_bounded = classmethod(_marker_is_bounded)
        _claim_is_expired = staticmethod(_claim_is_expired)
        _claim_row_failure = _claim_row_failure
        _bounded_claim_parts = staticmethod(_bounded_claim_parts)
        _claim_active_marker = _claim_active_marker
        _claim_pending_marker = _claim_pending_marker
        _claim_review_marker = _claim_review_marker
        _reserve_row_failure = _reserve_row_failure
        _review_reservation_inputs_valid = staticmethod(
            _review_reservation_inputs_valid
        )
        _reserve_marker_state = _reserve_marker_state
        _finalize_review_settlement_locked = _finalize_review_settlement_locked
        _apply_staged_turn_locked = _apply_staged_turn_locked

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or resolve_tasks_db_path()
        self._init_db()

    def acquire_review_mutation_lock(
        self, *, timeout: float | None = 30.0
    ) -> ReviewMutationLock:
        """Serialize checkpoint mutation and tombstone cleanup across workers."""
        return acquire_review_mutation_lock(self.db_path, timeout)

    @staticmethod
    def _connection(path: str):
        """Return the shared SQLite connection policy for private helpers."""
        return sqlite_connection(path)

    def _init_db(self) -> None:
        _init_db(self)

    @contextmanager
    def write(self):
        """Open the public transaction context for one store operation."""
        with write(self) as connection:
            yield connection

    @contextmanager
    def _write(self):
        """Compatibility alias for the public transaction context."""
        with _write(self) as connection:
            yield connection

    @staticmethod
    def _context(row: sqlite3.Row | tuple[Any, ...]) -> StoredBusinessContext:
        return _context(row)

    @staticmethod
    def _turn(row: tuple[Any, ...]) -> StoredTurn:
        return _turn(row)

    def load_context(self, key: str) -> StoredBusinessContext | None:
        """Load the durable business context for a conversation key."""
        return load_context(self, key)

    def load_turn(self, key: str, turn_id: str) -> StoredTurn | None:
        """Load one turn without creating a new pending proposal."""
        return load_turn(self, key, turn_id)

    def begin_turn(
        self, key: str, turn_id: str, operation: str, base_version: int
    ) -> BeginTurnResult:
        """Create or replay one in-progress terminal turn row."""
        return begin_turn(self, key, turn_id, operation, base_version)

    @staticmethod
    def _upsert_review_checkpoint_cleanup(
        connection: sqlite3.Connection,
        entry: _ReviewCleanupEntry,
    ) -> None:
        _upsert_review_checkpoint_cleanup(connection, entry)

    def _register_review_candidate_locked(
        self, entry: _ReviewCleanupEntry
    ) -> bool:
        return _register_review_candidate_locked(self, entry)

    def _register_review_candidate(
        self, request: _RegisterCandidateCall
    ) -> bool:
        return register_review_candidate(self, request)

    @staticmethod
    def _staged_turn_matches(
        row: sqlite3.Row | tuple[Any, ...],
        staged: StagedTurn,
        result_json: str,
        delta_json: str,
    ) -> bool:
        return _staged_turn_matches(row, staged, result_json, delta_json)

    def stage_turn(
        self, key: str, turn_id: str, staged: StagedTurn
    ) -> StoredTurn:
        """Persist a bounded terminal proposal before ledger settlement."""
        return stage_turn(self, key, turn_id, staged)

    def _claim_review_settlement(
        self, request: _ReviewClaimRequest
    ) -> ReviewSettlementClaim:
        return _claim_review_settlement(self, request)

    def _reserve_review_settlement(
        self, request: _ReviewReservationRequest
    ) -> ReviewSettlementClaim:
        return _reserve_review_settlement(self, request)

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

    def _finalize_review_settlement(
        self, request: _ReviewFinalizeCall
    ) -> bool:
        return _finalize_review_settlement(self, request)

    def mark_review_settlement_failed(
        self,
        key: str,
        turn_id: str,
        *,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Persist a terminal failure for a malformed or abandoned marker."""
        return _mark_review_settlement_failed(
            self,
            _ReviewFailureCall(
                key=key,
                turn_id=turn_id,
                mutation_lock_held=mutation_lock_held,
            ),
        )

    def update_review_settlement_metadata(
        self,
        key: str,
        turn_id: str,
        updates: Mapping[str, Any],
    ) -> bool:
        """Update private Review metadata for compatibility test seams."""
        return _update_review_settlement_metadata(
            self,
            _ReviewMetadataCall(
                key=key,
                turn_id=turn_id,
                updates=updates,
            ),
        )

    def commit_staged_turn(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> SettlementResult:
        """Atomically apply a staged turn or return its existing commit."""
        bound = _COMMIT_STAGED_TURN_SIGNATURE.bind(self, *args, **kwargs)
        bound.apply_defaults()
        result = _commit_staged_turn(
            self,
            _CommitCall(
                key=bound.arguments["key"],
                turn_id=bound.arguments["turn_id"],
                expected_ledger_version=bound.arguments[
                    "expected_ledger_version"
                ],
                ledger_version=bound.arguments["ledger_version"],
                mutation_lock_held=bound.arguments["mutation_lock_held"],
            ),
        )
        if (
            bound.arguments["mutation_lock_held"]
            and result.state == "committed"
        ):
            logger.debug("conversation turn committed")
        return result

    setattr(
        commit_staged_turn,
        "__signature__",
        _COMMIT_STAGED_TURN_SIGNATURE,
    )

    def mark_turn_failed(self, key: str, turn_id: str) -> None:
        """Mark a context turn failed without changing its result payload."""
        with self._write() as connection:
            connection.execute(
                "UPDATE conversation_turns SET state='failed', "
                "updated_at=? WHERE conversation_key=? AND turn_id=?",
                (_now(), key, turn_id),
            )

    def _prepare_tombstone_candidates(
        self, connection: sqlite3.Connection, key: str, now: str
    ) -> set[str]:
        """Collect candidates and fail pending Review markers.

        The caller owns the transaction and mutation lock.
        """
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
                    _ReviewMarkerWriteRequest(
                        connection=connection,
                        key=key,
                        turn_id=turn_id,
                        decoded=decoded,
                        marker=failed_marker,
                        now=now,
                    )
                )
        return candidates

    def tombstone(
        self, key: str, *, mutation_lock_held: bool = False
    ) -> tuple[str, ...]:
        """Tombstone a conversation and return candidate threads to clean."""
        if not mutation_lock_held:
            with self.acquire_review_mutation_lock():
                return self.tombstone(key, mutation_lock_held=True)
        now = _now()
        with self._write() as connection:
            candidates = self._prepare_tombstone_candidates(
                connection, key, now
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
        """Mark durable candidate-checkpoint cleanup complete."""
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
        """Remove expired staged turns while holding the mutation fence."""
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


install_store_facades(ConversationContextStore)


# Install private marker seams without changing the public store class identity.
setattr(
    ConversationContextStore,
    "_review_context_state",
    staticmethod(_review_context_state),
)
setattr(
    ConversationContextStore, "_review_record", staticmethod(_review_record)
)
setattr(
    ConversationContextStore,
    "_with_review_record",
    staticmethod(_with_review_record),
)
setattr(
    ConversationContextStore,
    "_claim_datetime",
    staticmethod(_claim_datetime),
)
setattr(
    ConversationContextStore,
    "_write_review_marker",
    classmethod(_write_review_marker),
)
setattr(ConversationContextStore, "_marker_fence", staticmethod(_marker_fence))
setattr(
    ConversationContextStore,
    "_marker_operation",
    staticmethod(_marker_operation),
)
setattr(
    ConversationContextStore,
    "_marker_stable_thread_id",
    staticmethod(_marker_stable_thread_id),
)
setattr(
    ConversationContextStore,
    "_turn_id_is_bounded",
    staticmethod(_turn_id_is_bounded),
)
setattr(
    ConversationContextStore,
    "_report_revision_is_bounded",
    staticmethod(_report_revision_is_bounded),
)
setattr(
    ConversationContextStore,
    "_bounded_marker_fields",
    classmethod(_bounded_marker_fields),
)
setattr(
    ConversationContextStore,
    "_stable_marker_matches_key",
    staticmethod(_stable_marker_matches_key),
)
setattr(
    ConversationContextStore,
    "_marker_candidate_is_bounded",
    staticmethod(_marker_candidate_is_bounded),
)
setattr(
    ConversationContextStore,
    "_marker_is_bounded",
    classmethod(_marker_is_bounded),
)
setattr(
    ConversationContextStore,
    "_claim_is_expired",
    staticmethod(_claim_is_expired),
)
setattr(ConversationContextStore, "_claim_row_failure", _claim_row_failure)
setattr(
    ConversationContextStore,
    "_bounded_claim_parts",
    staticmethod(_bounded_claim_parts),
)
setattr(ConversationContextStore, "_claim_active_marker", _claim_active_marker)
setattr(
    ConversationContextStore, "_claim_pending_marker", _claim_pending_marker
)
setattr(ConversationContextStore, "_claim_review_marker", _claim_review_marker)
setattr(ConversationContextStore, "_reserve_row_failure", _reserve_row_failure)
setattr(
    ConversationContextStore,
    "_review_reservation_inputs_valid",
    staticmethod(_review_reservation_inputs_valid),
)
setattr(
    ConversationContextStore, "_reserve_marker_state", _reserve_marker_state
)
setattr(
    ConversationContextStore,
    "_finalize_review_settlement_locked",
    _finalize_review_settlement_locked,
)
setattr(
    ConversationContextStore,
    "_apply_staged_turn_locked",
    _apply_staged_turn_locked,
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
