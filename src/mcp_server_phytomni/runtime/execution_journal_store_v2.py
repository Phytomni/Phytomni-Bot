# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite provider for the execution-keyed V2 public journal."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ..storage.path_policy import IdFactory
from .execution_event_limits import (
    DEFAULT_EXECUTION_EVENT_LIMITS,
    ExecutionEventLimits,
)
from .execution_journal_schema import migrate_execution_journal_v2
from .execution_journal_v2 import (
    ExecutionEventIntentV2,
    ExecutionEventType,
    ExecutionEventV2,
    ExecutionProjectionV2,
    parse_execution_event_v2,
)
from .execution_projection_v2 import (
    apply_execution_event_v2,
    empty_execution_projection_v2,
    fold_execution_events_v2,
)

_EVENT_SELECT = (
    "execution_id, seq, event_id, source_idempotency_key, schema_version, "
    "event_type, status, occurred_at, source, span_id, parent_span_id, "
    "work_unit_id, attempt, summary_json, public_payload_json, target_json"
)


class ExecutionJournalNotFoundError(LookupError):
    """An execution is unknown, foreign, or tombstoned."""


class ExecutionJournalPublicationFenceError(RuntimeError):
    """A superseded provider join attempted to publish a public fact."""


@dataclass(frozen=True, slots=True)
class ExecutionJournalPage:
    """One bounded durable history page."""

    items: tuple[ExecutionEventV2, ...]
    next_after_seq: int
    has_more: bool
    gaps: tuple[ExecutionSequenceGap, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionSequenceGap:
    """An explicit interval removed by bounded retention."""

    first_missing_seq: int
    last_missing_seq: int


@runtime_checkable
class ExecutionJournal(Protocol):
    """Storage-neutral append and replay boundary."""

    def append(
        self,
        execution_id: str,
        *,
        owner: str,
        intent: ExecutionEventIntentV2,
    ) -> ExecutionEventV2: ...

    def list_events(
        self,
        execution_id: str,
        *,
        owner: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> ExecutionJournalPage | None: ...

    def get_event(
        self,
        execution_id: str,
        event_id: str,
        *,
        owner: str,
    ) -> ExecutionEventV2 | None: ...

    def get_projection(
        self,
        execution_id: str,
        *,
        owner: str,
    ) -> ExecutionProjectionV2: ...


class SQLiteExecutionJournal:
    """Atomic SQLite implementation co-located with the run registry."""

    def __init__(
        self,
        db_path: str,
        *,
        event_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
        limits: ExecutionEventLimits = DEFAULT_EXECUTION_EVENT_LIMITS,
        expected_provider_join_lease_token: str | None = None,
    ) -> None:
        if expected_provider_join_lease_token is not None and (
            not expected_provider_join_lease_token
            or len(expected_provider_join_lease_token) > 128
        ):
            raise ValueError("invalid provider join lease token")
        self.db_path = db_path
        self._event_id_factory = event_id_factory or (
            lambda: IdFactory().new_id("evt")
        )
        self._clock = clock or _now_iso
        self._limits = limits
        self._expected_provider_join_lease_token = (
            expected_provider_join_lease_token
        )
        self._init_db()

    def _init_db(self) -> None:
        from .run_registry import RunRegistry

        RunRegistry(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            migrate_execution_journal_v2(connection)
            connection.commit()

    def append(
        self,
        execution_id: str,
        *,
        owner: str,
        intent: ExecutionEventIntentV2,
    ) -> ExecutionEventV2:
        """Atomically allocate a sequence and commit one safe event."""
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            self._authorize_publication(connection, owner, execution_id)
            event = self._append_locked(
                connection,
                execution_id=execution_id,
                owner=owner,
                intent=intent,
            )
            connection.commit()
            return event

    def append_provider_trace_batch(
        self,
        execution_id: str,
        *,
        owner: str,
        work_unit_id: str,
        expected_work_revision: int,
        cursor: str | None,
        source_revision: int,
        adapter_version: str,
        overlap_identities: tuple[str, ...],
        contact_at: str,
        health: str,
        intents: tuple[ExecutionEventIntentV2, ...],
    ) -> tuple[ExecutionEventV2, ...]:
        """Commit public trace facts and their private cursor in one TX."""
        _validate_provider_trace_batch(
            expected_work_revision=expected_work_revision,
            cursor=cursor,
            source_revision=source_revision,
            adapter_version=adapter_version,
            overlap_identities=overlap_identities,
            contact_at=contact_at,
            health=health,
            intents=intents,
        )
        overlap_json = json.dumps(
            overlap_identities, ensure_ascii=True, separators=(",", ":")
        )
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            self._authorize_publication(connection, owner, execution_id)
            events = tuple(
                self._append_locked(
                    connection,
                    execution_id=execution_id,
                    owner=owner,
                    intent=intent,
                )
                for intent in intents
            )
            updated_at = self._clock()
            updated = connection.execute(
                "UPDATE execution_work_units SET provider_trace_cursor = ?, "
                "provider_trace_revision = ?, "
                "provider_trace_adapter_version = ?, "
                "provider_trace_overlap_json = ?, "
                "provider_trace_contact_at = ?, provider_trace_health = ?, "
                "updated_at = ?, revision = revision + 1 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND work_unit_id = ? AND revision = ?",
                (
                    cursor,
                    source_revision,
                    adapter_version,
                    overlap_json,
                    contact_at,
                    health,
                    updated_at,
                    owner,
                    execution_id,
                    work_unit_id,
                    expected_work_revision,
                ),
            )
            if updated.rowcount != 1:
                raise ExecutionJournalPublicationFenceError(
                    "provider_trace_revision_conflict"
                )
            connection.commit()
            return events

    def _append_locked(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        owner: str,
        intent: ExecutionEventIntentV2,
    ) -> ExecutionEventV2:
        """Append one event inside an already-authorized write TX."""
        if intent.idempotency_key is not None:
            existing = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
                "WHERE owner_ref = ? AND execution_id = ? "
                "AND source_idempotency_key = ?",
                (owner, execution_id, intent.idempotency_key),
            ).fetchone()
            if existing is None:
                alias = connection.execute(
                    "SELECT event_id FROM execution_event_idempotency_v2 "
                    "WHERE owner_ref = ? AND execution_id = ? "
                    "AND source_idempotency_key = ?",
                    (owner, execution_id, intent.idempotency_key),
                ).fetchone()
                if alias is not None:
                    existing = connection.execute(
                        f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
                        "WHERE owner_ref = ? AND execution_id = ? "
                        "AND event_id = ?",
                        (owner, execution_id, alias[0]),
                    ).fetchone()
            if existing is not None:
                return _event_from_row(existing)
        occurred_at = self._clock()
        coalesced = self._coalesced_progress(
            connection,
            owner,
            execution_id,
            intent,
            occurred_at,
        )
        if coalesced is not None:
            self._save_idempotency_alias(
                connection,
                owner,
                execution_id,
                intent.idempotency_key,
                coalesced.event_id,
                occurred_at,
            )
            return coalesced
        projection = self._load_or_rebuild_projection(
            connection, owner, execution_id
        )
        seq = projection.latest_seq + 1
        event = intent.materialize(
            execution_id=execution_id,
            seq=seq,
            event_id=self._event_id_factory(),
            occurred_at=occurred_at,
        )
        connection.execute(
            "INSERT INTO execution_events_v2 ("
            "owner_ref, execution_id, seq, event_id, "
            "source_idempotency_key, schema_version, event_type, status, "
            "occurred_at, source, span_id, parent_span_id, work_unit_id, "
            "attempt, summary_json, public_payload_json, target_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            _event_row(owner, event, occurred_at),
        )
        self._save_idempotency_alias(
            connection,
            owner,
            execution_id,
            intent.idempotency_key,
            event.event_id,
            occurred_at,
        )
        projection = apply_execution_event_v2(projection, event)
        self._save_projection(connection, owner, projection, occurred_at)
        self._apply_retention(connection, owner, execution_id)
        return event

    def list_events(
        self,
        execution_id: str,
        *,
        owner: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> ExecutionJournalPage | None:
        """Return a bounded page strictly after the accepted cursor."""
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        page_size = self._limits.resolve_page_size(limit)
        with sqlite3.connect(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            rows = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
                "WHERE owner_ref = ? AND execution_id = ? AND seq > ? "
                "ORDER BY seq ASC LIMIT ?",
                (owner, execution_id, after_seq, page_size + 1),
            ).fetchall()
            latest_row = connection.execute(
                "SELECT latest_seq FROM execution_projection_v2 "
                "WHERE owner_ref = ? AND execution_id = ?",
                (owner, execution_id),
            ).fetchone()
        has_more = len(rows) > page_size
        events = tuple(_event_from_row(row) for row in rows[:page_size])
        next_after_seq = events[-1].seq if events else after_seq
        latest_seq = int(latest_row[0]) if latest_row is not None else 0
        return ExecutionJournalPage(
            items=events,
            next_after_seq=next_after_seq,
            has_more=has_more,
            gaps=_sequence_gaps(
                after_seq,
                events,
                latest_seq=latest_seq,
                has_more=has_more,
            ),
        )

    def get_event(
        self,
        execution_id: str,
        event_id: str,
        *,
        owner: str,
    ) -> ExecutionEventV2 | None:
        """Read one owned event by opaque stable identity."""
        with sqlite3.connect(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            row = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
                "WHERE owner_ref = ? AND execution_id = ? AND event_id = ?",
                (owner, execution_id, event_id),
            ).fetchone()
        return None if row is None else _event_from_row(row)

    def get_projection(
        self,
        execution_id: str,
        *,
        owner: str,
    ) -> ExecutionProjectionV2:
        """Return a valid cache or rebuild in memory from retained facts."""
        with sqlite3.connect(self.db_path) as connection:
            self._authorize(connection, owner, execution_id)
            return self._load_or_rebuild_projection(
                connection, owner, execution_id
            )

    def tombstone_execution(self, execution_id: str, *, owner: str) -> None:
        """Make V2 data inaccessible and purge it without touching V1 rows."""
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._authorize(connection, owner, execution_id)
            now = self._clock()
            connection.execute(
                "UPDATE runs SET execution_tombstoned_at = ? "
                "WHERE user_id = ? AND execution_id = ?",
                (now, owner, execution_id),
            )
            for table in (
                "execution_events_v2",
                "execution_event_idempotency_v2",
                "execution_spans",
                "execution_work_units",
                "execution_projection_v2",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE owner_ref = ? "
                    "AND execution_id = ?",
                    (owner, execution_id),
                )
            connection.commit()

    @staticmethod
    def _save_idempotency_alias(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
        source_key: str | None,
        event_id: str,
        created_at: str,
    ) -> None:
        if source_key is None:
            return
        connection.execute(
            "INSERT OR IGNORE INTO execution_event_idempotency_v2 ("
            "owner_ref, execution_id, source_idempotency_key, event_id, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (owner, execution_id, source_key, event_id, created_at),
        )

    def _coalesced_progress(
        self,
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
        intent: ExecutionEventIntentV2,
        occurred_at: str,
    ) -> ExecutionEventV2 | None:
        if intent.type not in {
            ExecutionEventType.SPAN_PROGRESS,
            ExecutionEventType.WORK_UNIT_PROGRESS,
        }:
            return None
        row = connection.execute(
            f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
            "WHERE owner_ref = ? AND execution_id = ? AND event_type = ? "
            "AND span_id = ? AND COALESCE(work_unit_id, '') = ? "
            "ORDER BY seq DESC LIMIT 1",
            (
                owner,
                execution_id,
                intent.type.value,
                intent.span_id,
                intent.work_unit_id or "",
            ),
        ).fetchone()
        if row is None:
            return None
        previous = _event_from_row(row)
        previous_ms = _utc_millis(previous.occurred_at)
        current_ms = _utc_millis(occurred_at)
        if self._limits.should_coalesce_progress(previous_ms, current_ms):
            return previous
        return None

    def _load_or_rebuild_projection(
        self,
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> ExecutionProjectionV2:
        row = connection.execute(
            "SELECT projection_json FROM execution_projection_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            (owner, execution_id),
        ).fetchone()
        if row is not None:
            try:
                return ExecutionProjectionV2.model_validate_json(row[0])
            except (ValueError, TypeError):
                pass
        rows = connection.execute(
            f"SELECT {_EVENT_SELECT} FROM execution_events_v2 "
            "WHERE owner_ref = ? AND execution_id = ? ORDER BY seq ASC",
            (owner, execution_id),
        ).fetchall()
        if not rows:
            return empty_execution_projection_v2(execution_id)
        return fold_execution_events_v2(
            execution_id,
            (_event_from_row(event_row) for event_row in rows),
        )

    @staticmethod
    def _save_projection(
        connection: sqlite3.Connection,
        owner: str,
        projection: ExecutionProjectionV2,
        updated_at: str,
    ) -> None:
        encoded = json.dumps(
            projection.model_dump(mode="json"), ensure_ascii=False
        )
        connection.execute(
            "INSERT INTO execution_projection_v2 ("
            "owner_ref, execution_id, latest_seq, first_available_seq, "
            "projection_json, projection_revision, updated_at) "
            "VALUES (?, ?, ?, 1, ?, 1, ?) ON CONFLICT(owner_ref, execution_id) "
            "DO UPDATE SET latest_seq = excluded.latest_seq, "
            "projection_json = excluded.projection_json, "
            "projection_revision = execution_projection_v2.projection_revision + 1, "
            "updated_at = excluded.updated_at",
            (
                owner,
                projection.execution_id,
                projection.latest_seq,
                encoded,
                updated_at,
            ),
        )

    def _apply_retention(
        self,
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> None:
        count_row = connection.execute(
            "SELECT COUNT(*) FROM execution_events_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            (owner, execution_id),
        ).fetchone()
        excess = int(count_row[0]) - self._limits.max_events_per_run
        if excess <= 0:
            return
        candidates = connection.execute(
            "SELECT seq FROM execution_events_v2 WHERE owner_ref = ? "
            "AND execution_id = ? AND event_type IN (?, ?) "
            "ORDER BY seq ASC LIMIT ?",
            (
                owner,
                execution_id,
                ExecutionEventType.SPAN_PROGRESS.value,
                ExecutionEventType.WORK_UNIT_PROGRESS.value,
                excess,
            ),
        ).fetchall()
        if not candidates:
            return
        sequence_values = tuple(int(row[0]) for row in candidates)
        placeholders = ",".join("?" for _ in sequence_values)
        event_ids = connection.execute(
            f"SELECT event_id FROM execution_events_v2 WHERE owner_ref = ? "
            f"AND execution_id = ? AND seq IN ({placeholders})",
            (owner, execution_id, *sequence_values),
        ).fetchall()
        if event_ids:
            id_placeholders = ",".join("?" for _ in event_ids)
            connection.execute(
                "DELETE FROM execution_event_idempotency_v2 "
                f"WHERE owner_ref = ? AND execution_id = ? AND event_id IN "
                f"({id_placeholders})",
                (owner, execution_id, *(row[0] for row in event_ids)),
            )
        connection.execute(
            f"DELETE FROM execution_events_v2 WHERE owner_ref = ? "
            f"AND execution_id = ? AND seq IN ({placeholders})",
            (owner, execution_id, *sequence_values),
        )
        first = connection.execute(
            "SELECT MIN(seq) FROM execution_events_v2 WHERE owner_ref = ? "
            "AND execution_id = ?",
            (owner, execution_id),
        ).fetchone()
        if first is not None and first[0] is not None:
            connection.execute(
                "UPDATE execution_projection_v2 SET first_available_seq = ? "
                "WHERE owner_ref = ? AND execution_id = ?",
                (int(first[0]), owner, execution_id),
            )

    @staticmethod
    def _authorize(
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> None:
        if not owner or not execution_id:
            raise ExecutionJournalNotFoundError(execution_id)
        found = connection.execute(
            "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
            "AND execution_tombstoned_at IS NULL LIMIT 1",
            (owner, execution_id),
        ).fetchone()
        if found is None:
            raise ExecutionJournalNotFoundError(execution_id)

    def _authorize_publication(
        self,
        connection: sqlite3.Connection,
        owner: str,
        execution_id: str,
    ) -> None:
        """Fence the event insert inside the same write transaction."""
        token = self._expected_provider_join_lease_token
        if token is None:
            return
        found = connection.execute(
            "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
            "AND execution_tombstoned_at IS NULL "
            "AND execution_provider_join_lease_owner = ? "
            "AND execution_provider_join_lease_expires_at > ? LIMIT 1",
            (owner, execution_id, token, self._clock()),
        ).fetchone()
        if found is None:
            raise ExecutionJournalPublicationFenceError(
                "provider_join_lease_lost"
            )


def _event_row(
    owner: str,
    event: ExecutionEventV2,
    created_at: str,
) -> tuple[object, ...]:
    return (
        owner,
        event.execution_id,
        event.seq,
        event.event_id,
        event.idempotency_key,
        event.schema_version,
        event.type.value,
        event.status.value,
        event.occurred_at,
        event.source.value,
        event.span_id,
        event.parent_span_id,
        event.work_unit_id,
        event.attempt,
        json.dumps(event.summary.model_dump(mode="json"), ensure_ascii=False),
        json.dumps(
            event.public_payload.model_dump(mode="json"),
            ensure_ascii=False,
        ),
        (
            None
            if event.target is None
            else json.dumps(
                event.target.model_dump(mode="json"),
                ensure_ascii=False,
            )
        ),
        created_at,
    )


def _validate_provider_trace_batch(
    *,
    expected_work_revision: int,
    cursor: str | None,
    source_revision: int,
    adapter_version: str,
    overlap_identities: tuple[str, ...],
    contact_at: str,
    health: str,
    intents: tuple[ExecutionEventIntentV2, ...],
) -> None:
    if expected_work_revision < 0 or source_revision < 0:
        raise ValueError("provider trace revision must be non-negative")
    if cursor is not None and len(cursor) > 128:
        raise ValueError("provider trace cursor is too long")
    if not adapter_version or len(adapter_version) > 32:
        raise ValueError("invalid provider trace adapter version")
    if len(overlap_identities) > 64 or any(
        not identity or len(identity) > 128 for identity in overlap_identities
    ):
        raise ValueError("invalid provider trace overlap")
    if health not in {"healthy", "degraded", "unavailable"}:
        raise ValueError("invalid provider trace health")
    if len(intents) > 256:
        raise ValueError("provider trace event batch is too large")
    try:
        parsed_contact = datetime.fromisoformat(
            contact_at.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("invalid provider trace contact time") from exc
    if parsed_contact.utcoffset() is None:
        raise ValueError("provider trace contact time requires timezone")


def _event_from_row(row: tuple[object, ...]) -> ExecutionEventV2:
    target_json = row[15]
    return parse_execution_event_v2(
        {
            "schema_version": row[4],
            "execution_id": row[0],
            "seq": row[1],
            "event_id": row[2],
            "idempotency_key": row[3],
            "type": row[5],
            "status": row[6],
            "occurred_at": row[7],
            "source": row[8],
            "span_id": row[9],
            "parent_span_id": row[10],
            "work_unit_id": row[11],
            "attempt": row[12],
            "summary": json.loads(str(row[13])),
            "public_payload": json.loads(str(row[14])),
            "target": (
                None if target_json is None else json.loads(str(target_json))
            ),
        }
    )


def _sequence_gaps(
    after_seq: int,
    events: tuple[ExecutionEventV2, ...],
    *,
    latest_seq: int,
    has_more: bool,
) -> tuple[ExecutionSequenceGap, ...]:
    gaps: list[ExecutionSequenceGap] = []
    expected = after_seq + 1
    for event in events:
        if event.seq > expected:
            gaps.append(
                ExecutionSequenceGap(
                    first_missing_seq=expected,
                    last_missing_seq=event.seq - 1,
                )
            )
        expected = event.seq + 1
    if not has_more and latest_seq >= expected:
        gaps.append(
            ExecutionSequenceGap(
                first_missing_seq=expected,
                last_missing_seq=latest_seq,
            )
        )
    return tuple(gaps)


def _utc_millis(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
