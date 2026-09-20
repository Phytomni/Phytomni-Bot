# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite persistence boundary for public execution events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .execution_event_limits import (
    DEFAULT_EXECUTION_EVENT_LIMITS,
    ExecutionEventLimitError,
    ExecutionEventLimits,
)
from .execution_event_projection import (
    apply_execution_event,
    fold_execution_events,
)
from .execution_events import (
    ExecutionEventIntent,
    ExecutionEventV1,
    RunEventProjectionV1,
    parse_execution_event,
    parse_run_event_projection,
)
from .execution_store_support_v2 import event_store_settings

_CREATE_RUN_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq > 0),
    event_id TEXT NOT NULL,
    idempotency_key TEXT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT,
    parent_event_id TEXT,
    ignorable INTEGER NOT NULL DEFAULT 0 CHECK (ignorable IN (0, 1)),
    summary_json TEXT NOT NULL,
    public_payload_json TEXT NOT NULL,
    target_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    UNIQUE (run_id, idempotency_key),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""

_CREATE_RUN_EVENT_PROJECTION_DDL = """
CREATE TABLE IF NOT EXISTS run_event_projection (
    run_id TEXT PRIMARY KEY,
    latest_seq INTEGER NOT NULL CHECK (latest_seq >= 0),
    projection_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
"""

_CREATE_EVENT_ID_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_event_id "
    "ON run_events(event_id)"
)
_CREATE_KIND_SEQUENCE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_run_events_kind_seq "
    "ON run_events(run_id, kind, seq)"
)
_CREATE_OCCURRED_AT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_run_events_occurred_at "
    "ON run_events(occurred_at)"
)

_EVENT_SELECT = (
    "run_id, seq, event_id, idempotency_key, schema_version, occurred_at, "
    "kind, status, task_id, parent_event_id, ignorable, summary_json, "
    "public_payload_json, target_json"
)


class ExecutionEventRunNotFoundError(LookupError):
    """The requested run is unknown or not owned by the caller."""


@dataclass(frozen=True, slots=True)
class ExecutionEventPage:
    """One bounded replay page and its accepted continuation cursor."""

    items: tuple[ExecutionEventV1, ...]
    next_after_seq: int
    has_more: bool


@runtime_checkable
class ExecutionEventStore(Protocol):
    """Storage-neutral durable public event contract."""

    def append(
        self,
        run_id: str,
        *,
        owner: str,
        intent: ExecutionEventIntent,
    ) -> ExecutionEventV1:
        """Append one validated event under its owner-scoped run."""
        raise NotImplementedError

    def list_events(
        self,
        run_id: str,
        *,
        owner: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> ExecutionEventPage | None:
        """Return a bounded ordered replay page for a visible run."""

    def get_event(
        self,
        run_id: str,
        event_id: str,
        *,
        owner: str,
    ) -> ExecutionEventV1 | None:
        """Read one owner-scoped event by its stable identity."""

    def get_projection(
        self,
        run_id: str,
        *,
        owner: str,
    ) -> RunEventProjectionV1 | None:
        """Fold the visible run into its current public projection."""


class SQLiteExecutionEventStore:
    """Durable event store co-located with the Bot run registry database."""

    def __init__(
        self,
        db_path: str,
        *,
        event_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
        limits: ExecutionEventLimits = DEFAULT_EXECUTION_EVENT_LIMITS,
    ) -> None:
        self.db_path = db_path
        self._settings = event_store_settings(event_id_factory, clock, limits)
        self._init_db()

    def _init_db(self) -> None:
        """Create additive V1 tables without rewriting legacy run rows."""
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_CREATE_RUN_EVENTS_DDL)
            connection.execute(_CREATE_RUN_EVENT_PROJECTION_DDL)
            connection.execute(_CREATE_EVENT_ID_INDEX)
            connection.execute(_CREATE_KIND_SEQUENCE_INDEX)
            connection.execute(_CREATE_OCCURRED_AT_INDEX)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append(
        self,
        run_id: str,
        *,
        owner: str,
        intent: ExecutionEventIntent,
    ) -> ExecutionEventV1:
        """Atomically allocate sequence, persist, and return one event."""
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            if not _owned_run_exists(connection, run_id, owner):
                raise ExecutionEventRunNotFoundError(run_id)
            if intent.idempotency_key is not None:
                existing = connection.execute(
                    f"SELECT {_EVENT_SELECT} FROM run_events "
                    "WHERE run_id = ? AND idempotency_key = ?",
                    (run_id, intent.idempotency_key),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return _event_from_row(existing)
            occurred_at = self._settings.clock()
            coalesced = _coalesced_progress_event(
                connection,
                run_id,
                intent,
                occurred_at=occurred_at,
                limits=self._settings.limits,
            )
            if coalesced is not None:
                connection.commit()
                return coalesced
            pruned = _make_event_capacity(
                connection,
                run_id,
                limits=self._settings.limits,
            )
            row = connection.execute(
                "SELECT MAX(high_water) + 1 FROM ("
                "SELECT COALESCE(MAX(seq), 0) AS high_water FROM run_events "
                "WHERE run_id = ? UNION ALL "
                "SELECT COALESCE(MAX(latest_seq), 0) AS high_water "
                "FROM run_event_projection WHERE run_id = ?)",
                (run_id, run_id),
            ).fetchone()
            seq = int(row[0])
            event = intent.materialize(
                run_id=run_id,
                seq=seq,
                event_id=self._settings.event_id_factory(),
                occurred_at=occurred_at,
            )
            connection.execute(
                "INSERT INTO run_events ("
                "run_id, seq, event_id, idempotency_key, schema_version, "
                "occurred_at, kind, status, task_id, parent_event_id, "
                "ignorable, summary_json, public_payload_json, target_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?)",
                _event_row(event, created_at=occurred_at),
            )
            projection = (
                _fold_stored_events(connection, run_id, exclude_latest=True)
                if pruned
                else _projection_before_append(
                    connection,
                    run_id,
                    expected_latest_seq=seq - 1,
                )
            )
            _upsert_projection(
                connection,
                apply_execution_event(projection, event),
                updated_at=occurred_at,
            )
            connection.commit()
            return event
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def list_events(
        self,
        run_id: str,
        *,
        owner: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> ExecutionEventPage | None:
        """Return an owner-scoped ordered page after an accepted cursor."""
        if after_seq < 0:
            raise ValueError("invalid_after_seq")
        page_size = DEFAULT_EXECUTION_EVENT_LIMITS.resolve_page_size(limit)
        connection = sqlite3.connect(self.db_path)
        try:
            if not _owned_run_exists(connection, run_id, owner):
                return None
            rows = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM run_events "
                "WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                (run_id, after_seq, page_size + 1),
            ).fetchall()
        finally:
            connection.close()
        has_more = len(rows) > page_size
        events = tuple(_event_from_row(row) for row in rows[:page_size])
        next_after_seq = events[-1].seq if events else after_seq
        return ExecutionEventPage(events, next_after_seq, has_more)

    def get_event(
        self,
        run_id: str,
        event_id: str,
        *,
        owner: str,
    ) -> ExecutionEventV1 | None:
        """Return one event only when its parent run is owner-visible."""
        connection = sqlite3.connect(self.db_path)
        try:
            if not _owned_run_exists(connection, run_id, owner):
                return None
            row = connection.execute(
                f"SELECT {_EVENT_SELECT} FROM run_events "
                "WHERE run_id = ? AND event_id = ?",
                (run_id, event_id),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _event_from_row(row)

    def get_projection(
        self,
        run_id: str,
        *,
        owner: str,
    ) -> RunEventProjectionV1 | None:
        """Return a valid cache, rebuilding it from the ledger if necessary."""
        connection = sqlite3.connect(self.db_path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            if not _owned_run_exists(connection, run_id, owner):
                connection.commit()
                return None
            latest_row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM run_events "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            latest_seq = int(latest_row[0])
            projection = _read_cached_projection(connection, run_id)
            if projection is None or projection.latest_seq != latest_seq:
                projection = _fold_stored_events(connection, run_id)
                _upsert_projection(
                    connection,
                    projection,
                    updated_at=self._settings.clock(),
                )
            connection.commit()
            return projection
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def _owned_run_exists(
    connection: sqlite3.Connection, run_id: str, owner: str
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, owner),
        ).fetchone()
        is not None
    )


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _event_row(
    event: ExecutionEventV1, *, created_at: str
) -> tuple[object, ...]:
    return (
        event.run_id,
        event.seq,
        event.event_id,
        event.idempotency_key,
        event.schema_version,
        event.occurred_at,
        event.kind,
        event.status,
        event.task_id,
        event.parent_event_id,
        int(event.ignorable),
        _compact_json(event.summary.model_dump(mode="json")),
        _compact_json(event.payload.model_dump(mode="json")),
        (
            None
            if event.target is None
            else _compact_json(event.target.model_dump(mode="json"))
        ),
        created_at,
    )


def _event_from_row(row: tuple[object, ...]) -> ExecutionEventV1:
    raw: dict[str, object] = {
        "run_id": row[0],
        "seq": row[1],
        "event_id": row[2],
        "schema_version": row[4],
        "occurred_at": row[5],
        "kind": row[6],
        "status": row[7],
        "ignorable": bool(row[10]),
        "summary": json.loads(str(row[11])),
        "payload": json.loads(str(row[12])),
    }
    for key, index in (
        ("idempotency_key", 3),
        ("task_id", 8),
        ("parent_event_id", 9),
    ):
        if row[index] is not None:
            raw[key] = row[index]
    if row[13] is not None:
        raw["target"] = json.loads(str(row[13]))
    return parse_execution_event(raw)


def _read_cached_projection(
    connection: sqlite3.Connection,
    run_id: str,
) -> RunEventProjectionV1 | None:
    row = connection.execute(
        "SELECT latest_seq, projection_json FROM run_event_projection "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        projection = parse_run_event_projection(json.loads(str(row[1])))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if projection.run_id != run_id or projection.latest_seq != int(row[0]):
        return None
    return projection


def _stored_events(
    connection: sqlite3.Connection,
    run_id: str,
) -> tuple[ExecutionEventV1, ...]:
    rows = connection.execute(
        f"SELECT {_EVENT_SELECT} FROM run_events "
        "WHERE run_id = ? ORDER BY seq ASC",
        (run_id,),
    ).fetchall()
    return tuple(_event_from_row(row) for row in rows)


def _fold_stored_events(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    exclude_latest: bool = False,
) -> RunEventProjectionV1:
    events = _stored_events(connection, run_id)
    if exclude_latest:
        events = events[:-1]
    return fold_execution_events(run_id, events)


def _projection_before_append(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    expected_latest_seq: int,
) -> RunEventProjectionV1:
    projection = _read_cached_projection(connection, run_id)
    if projection is not None and projection.latest_seq == expected_latest_seq:
        return projection
    events = _stored_events(connection, run_id)
    return fold_execution_events(run_id, events[:-1])


def _upsert_projection(
    connection: sqlite3.Connection,
    projection: RunEventProjectionV1,
    *,
    updated_at: str,
) -> None:
    connection.execute(
        "INSERT INTO run_event_projection "
        "(run_id, latest_seq, projection_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET "
        "latest_seq = excluded.latest_seq, "
        "projection_json = excluded.projection_json, "
        "updated_at = excluded.updated_at",
        (
            projection.run_id,
            projection.latest_seq,
            _compact_json(projection.to_public_dict()),
            updated_at,
        ),
    )


def _coalesced_progress_event(
    connection: sqlite3.Connection,
    run_id: str,
    intent: ExecutionEventIntent,
    *,
    occurred_at: str,
    limits: ExecutionEventLimits,
) -> ExecutionEventV1 | None:
    if intent.kind != "phase.progress":
        return None
    row = connection.execute(
        f"SELECT {_EVENT_SELECT} FROM run_events "
        "WHERE run_id = ? AND kind = 'phase.progress' "
        "ORDER BY seq DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    previous = _event_from_row(row)
    previous_phase = getattr(previous.payload, "phase", None)
    current_phase = getattr(intent.payload, "phase", None)
    if previous_phase != current_phase:
        return None
    try:
        previous_ms = _iso_millis(previous.occurred_at)
        current_ms = _iso_millis(occurred_at)
    except ValueError:
        return None
    if limits.should_coalesce_progress(previous_ms, current_ms):
        return previous
    return None


def _iso_millis(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


def _make_event_capacity(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    limits: ExecutionEventLimits,
) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM run_events WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if int(row[0]) < limits.max_events_per_run:
        return False
    progress = connection.execute(
        "SELECT seq FROM run_events WHERE run_id = ? "
        "AND kind = 'phase.progress' ORDER BY seq ASC LIMIT 1",
        (run_id,),
    ).fetchone()
    if progress is None:
        raise ExecutionEventLimitError("event_volume_exceeded")
    connection.execute(
        "DELETE FROM run_events WHERE run_id = ? AND seq = ?",
        (run_id, int(progress[0])),
    )
    return True


def purge_execution_event_children(
    connection: sqlite3.Connection,
    run_ids: Sequence[str],
) -> None:
    """Remove event-owned rows before their parent run is deleted."""
    ids = tuple(run_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    for table in ("run_event_projection", "run_events"):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is not None:
            connection.execute(
                f"DELETE FROM {table} WHERE run_id IN ({placeholders})",
                ids,
            )
