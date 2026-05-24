# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run-scoped registry over the shared task database.

Owns the ``runs`` table in the same SQLite file as ``tasks`` so the
API and MCP paths read one source of truth. Child task ids are derived
from ``tasks.run_id`` on read (no denormalised column to drift).

Public dataclasses: RunSpec, RunFilter, Timestamps, RunRecord, RunRegistry.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config.defaults import ApiConfig
from .task_manager import TaskManager, resolve_tasks_db_path
from .task_reconcile import reconcile_task

__all__ = [
    "RunFilter",
    "RunRecord",
    "RunRegistry",
    "RunRequestInfo",
    "RunSpec",
    "Timestamps",
]

# Status vocabulary shared with the task layer. Mirrors the e2e poller
# so the same agent payloads aggregate consistently across MCP and API.
_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_FAILURE_STATUSES = frozenset({"failed", "error"})
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed"})

_CREATE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    dialogue_id TEXT,
    query TEXT,
    tool_name TEXT,
    model TEXT,
    request_json TEXT
)
"""

# Request-context columns added after the initial schema shipped.
# ``_init_db`` walks the list and runs an idempotent ``ALTER TABLE``
# per name so an upgraded database catches up to the same shape a
# fresh ``CREATE TABLE`` would produce.
_REQUEST_INFO_COLUMNS = (
    ("dialogue_id", "TEXT"),
    ("query", "TEXT"),
    ("tool_name", "TEXT"),
    ("model", "TEXT"),
    ("request_json", "TEXT"),
)

_CREATE_RUNS_USER_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)"
)
_CREATE_TASKS_RUN_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id)"
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _aggregate_status(task_statuses: List[str]) -> str:
    """Aggregate child task statuses into one run status.

    All success-like → ``succeeded``; any failure-like → ``failed``;
    otherwise ``running`` (the safe default while any child is still
    in-flight or in an unknown state).
    """
    lowered = [s.lower() for s in task_statuses if s]
    if any(s in _FAILURE_STATUSES for s in lowered):
        return "failed"
    if lowered and all(s in _SUCCESS_STATUSES for s in lowered):
        return "succeeded"
    return "running"


@dataclass(frozen=True)
class RunSpec:
    """Identity of one run: row primary key + owner/agent/origin.

    Attributes:
        run_id: Owning run id (caller-minted via ``IdFactory``).
        user_id: Authenticated user (``"anonymous"`` on the MCP path).
        agent: Public agent alias (e.g. ``"analyst"``).
        origin: ``"remote"`` for analysis-platform submissions,
            ``"local"`` for in-process synchronous runs.
    """

    run_id: str
    user_id: str
    agent: str
    origin: str


@dataclass(frozen=True)
class RunRequestInfo:
    """Per-request metadata persisted alongside the run row.

    These fields are captured at the request boundary so chat-ai's
    history page can render past conversations without re-deriving the
    surface from the agent payload. ``None`` is acceptable on every
    field so legacy MCP-only runs that predate the columns still hydrate
    without backfill.

    Attributes:
        dialogue_id: Chat-ai conversation id; groups runs into one
            visible thread.
        query: Verbatim user query text the agent saw.
        tool_name: MCP tool name dispatched (e.g. ``"KnowledgeAgent"``).
        model: OpenAI-compat model id when the request came through
            ``/v1/chat/completions``; ``None`` for native agent runs.
        request_json: Full JSON snapshot of the request body so an
            auditor can replay or diff the call.
    """

    dialogue_id: Optional[str] = None
    query: Optional[str] = None
    tool_name: Optional[str] = None
    model: Optional[str] = None
    request_json: Optional[str] = None


@dataclass(frozen=True)
class RunFilter:
    """Optional list-time filters for ``list_runs``.

    Attributes:
        status: Exact-match run status filter.
        agent: Exact-match agent alias filter.
        origin: Exact-match origin filter (``"local"``/``"remote"``).
    """

    status: Optional[str] = None
    agent: Optional[str] = None
    origin: Optional[str] = None


@dataclass(frozen=True)
class Timestamps:
    """Row timestamps and TTL expiry for one run.

    Attributes:
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
        expires_at: ISO-8601 lazy-purge deadline (only set on terminal).
    """

    created_at: str
    updated_at: str
    expires_at: Optional[str]


@dataclass(frozen=True)
class RunRecord:
    """Read view of one run row plus its child task ids.

    Logical groups are bundled into ``RunSpec`` (identity) and
    ``Timestamps`` (lifecycle) so the dataclass stays under the
    project's pylint ``max-attributes`` limit while still carrying every
    row field.

    Attributes:
        spec: Identity bundle (run_id, user_id, agent, origin).
        status: Aggregated run status (running/succeeded/failed).
        result: Parsed terminal result payload, when cached.
        error: Terminal error message, when cached.
        timestamps: Created / updated / expires-at bundle.
        task_ids: Child task ids (empty for sync runs).
        request_info: Per-request metadata captured at the HTTP boundary
            (dialogue_id / query / tool_name / model / request_json).
            Always populated; field values default to ``None`` for
            legacy rows that predate the columns.
    """

    spec: RunSpec
    status: str
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    timestamps: Timestamps
    task_ids: Tuple[str, ...]
    request_info: RunRequestInfo = RunRequestInfo()


class RunRegistry:
    """Run-level CRUD + aggregation over the shared task database.

    Shares ``resolve_tasks_db_path`` with ``TaskManager`` so a run row
    and its child tasks are always co-located in one SQLite file.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Open or create the run registry at ``db_path``.

        Args:
            db_path: Optional override (defaults to the configured
                shared task DB).
        """
        self.db_path = db_path or resolve_tasks_db_path()
        self._init_db()

    def _init_db(self) -> None:
        """Create the ``runs`` table and shared indices if missing.

        Eagerly initialises the ``tasks`` table via ``TaskManager`` (its
        constructor is idempotent) so the ``idx_tasks_run`` index can be
        created even when the registry is opened before any task write.
        The request-context columns are added via per-column
        ``ALTER TABLE`` so a database created before they shipped
        catches up without losing the existing rows; a fresh database
        already has them via ``CREATE TABLE`` and the ALTER simply
        raises ``OperationalError`` which is swallowed.
        """
        TaskManager(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_RUNS_DDL)
            for column, column_type in _REQUEST_INFO_COLUMNS:
                try:
                    conn.execute(
                        f"ALTER TABLE runs ADD COLUMN {column} {column_type}"
                    )
                except sqlite3.OperationalError:
                    pass
            conn.execute(_CREATE_RUNS_USER_INDEX)
            conn.execute(_CREATE_TASKS_RUN_INDEX)
            conn.commit()
        finally:
            conn.close()

    def create_run(
        self,
        spec: RunSpec,
        *,
        status: str = "running",
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        request_info: Optional[RunRequestInfo] = None,
    ) -> None:
        """Insert a new run row.

        Idempotent via ``INSERT OR REPLACE`` so a retried chokepoint
        write does not crash on the duplicate run_id key. The expiry is
        set immediately for terminal-on-creation runs (sync agents)
        using the OK / FAIL TTL knobs from ``ApiConfig``.

        Args:
            spec: Identity bundle (run_id, user_id, agent, origin).
            status: Initial run status; defaults to ``"running"``.
            result: Terminal result payload for sync runs.
            error: Terminal error message for failed sync runs.
            request_info: Per-request metadata captured at the HTTP
                boundary; ``None`` (default) leaves every column NULL
                so MCP-path runs that never see request context
                continue to write the same five fields as before.
        """
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        info = request_info or RunRequestInfo()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, user_id, agent, origin, status,
                    result_json, error, created_at, updated_at,
                    expires_at,
                    dialogue_id, query, tool_name, model, request_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.run_id,
                    spec.user_id,
                    spec.agent,
                    spec.origin,
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    now,
                    now,
                    expires_at,
                    info.dialogue_id,
                    info.query,
                    info.tool_name,
                    info.model,
                    info.request_json,
                ),
            )

    def update_request_info(
        self,
        run_id: str,
        *,
        owner: str,
        request_info: RunRequestInfo,
    ) -> bool:
        """Backfill request-context columns on an already-created run.

        The HTTP layer calls this after the agent dispatch returns, so
        the chokepoint that minted the run row never has to know about
        request context. Owner-scoped so a foreign-owned row cannot be
        retro-stamped.

        Args:
            run_id: Run id to update.
            owner: Required user id; mismatched owner is a silent miss.
            request_info: Field values to write.

        Returns:
            True when a row was updated; False when no owned row
            matched (caller logs but does not raise).
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    dialogue_id = ?,
                    query = ?,
                    tool_name = ?,
                    model = ?,
                    request_json = ?,
                    updated_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    request_info.dialogue_id,
                    request_info.query,
                    request_info.tool_name,
                    request_info.model,
                    request_info.request_json,
                    _now_iso(),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def get_run(self, run_id: str, *, owner: str) -> Optional[RunRecord]:
        """Return the run owned by ``owner`` or ``None``.

        Owner isolation is enforced at the SELECT so an unknown id and
        a foreign-owned id are indistinguishable to the caller (the API
        layer turns both into a 404 with no information leak).

        Args:
            run_id: Run id to look up.
            owner: Required user id; mismatched owner returns ``None``.

        Returns:
            ``RunRecord`` when present and owned by ``owner``,
            otherwise ``None``.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, query, tool_name, model, request_json
                FROM runs WHERE run_id = ? AND user_id = ?
                """,
                (run_id, owner),
            ).fetchone()
            if row is None:
                return None
            task_rows = conn.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? "
                "ORDER BY task_id",
                (run_id,),
            ).fetchall()
        return _row_to_record(run_id, row, task_rows)

    def list_runs(
        self,
        *,
        owner: str,
        run_filter: Optional[RunFilter] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[RunRecord]:
        """Return up to ``limit`` runs owned by ``owner``.

        Filters compose conjunctively. The result is ordered by
        ``created_at`` descending so a newcomer sees their most recent
        submissions first.

        Args:
            owner: Required user id; rows outside the owner are
                invisible.
            run_filter: Optional bundle of equality filters.
            limit: Maximum rows to return.
            offset: Skip this many leading rows (paging).

        Returns:
            Newest-first list of records.
        """
        run_filter = run_filter or RunFilter()
        where, params = _build_list_where(owner, run_filter)
        params.extend([limit, offset])
        records: List[RunRecord] = []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT run_id, user_id, agent, origin, status,
                       result_json, error, created_at, updated_at,
                       expires_at,
                       dialogue_id, query, tool_name, model, request_json
                FROM runs WHERE {where}
                ORDER BY created_at DESC, run_id
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
            for run_row in rows:
                task_rows = conn.execute(
                    "SELECT task_id FROM tasks WHERE run_id = ? "
                    "ORDER BY task_id",
                    (run_row[0],),
                ).fetchall()
                records.append(
                    _row_to_record(run_row[0], run_row[1:], task_rows)
                )
        return records

    async def reconcile(
        self, run_id: str, *, owner: str
    ) -> Optional[RunRecord]:
        """Refresh a non-terminal run by polling its child tasks.

        Terminal cached runs are returned as-is without any task probe;
        this is the "terminal → no re-poll" guard. Non-terminal runs
        call ``reconcile_task`` exactly once per child, aggregate, and
        on a fresh terminal write the result_json / error plus the OK
        or FAIL TTL ``expires_at``.

        Args:
            run_id: Run id to reconcile.
            owner: Owner check (404 isolation preserved).

        Returns:
            Updated ``RunRecord`` or ``None`` (unknown / not-owner).
        """
        current = self.get_run(run_id, owner=owner)
        if current is None or current.status in _TERMINAL_RUN_STATUSES:
            return current
        live: List[Dict[str, Any]] = []
        for task_id in current.task_ids:
            live.append(await reconcile_task(task_id))
        new_status = _aggregate_status([row["status"] for row in live])
        if new_status not in _TERMINAL_RUN_STATUSES:
            return self._touch_running(current, new_status)
        return self._settle_terminal(current, new_status, live)

    def purge_expired(self) -> int:
        """Delete runs whose ``expires_at`` has elapsed and their tasks.

        SQLite has no automatic CASCADE without ``PRAGMA foreign_keys``
        plus a schema migration, so cascade is performed in three
        explicit steps inside a single connection: select expired run
        ids, delete the child tasks rows, then delete the run rows.

        Returns:
            Number of run rows deleted.
        """
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            expired = [
                row[0]
                for row in conn.execute(
                    "SELECT run_id FROM runs "
                    "WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,),
                ).fetchall()
            ]
            if not expired:
                return 0
            placeholders = ",".join("?" for _ in expired)
            conn.execute(
                f"DELETE FROM tasks WHERE run_id IN ({placeholders})",
                tuple(expired),
            )
            conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({placeholders})",
                tuple(expired),
            )
        return len(expired)

    def _touch_running(self, current: RunRecord, status: str) -> RunRecord:
        """Persist a non-terminal status refresh (updates updated_at)."""
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE run_id = ?",
                (status, now, current.spec.run_id),
            )
        return RunRecord(
            spec=current.spec,
            status=status,
            result=current.result,
            error=current.error,
            timestamps=Timestamps(
                created_at=current.timestamps.created_at,
                updated_at=now,
                expires_at=current.timestamps.expires_at,
            ),
            task_ids=current.task_ids,
        )

    def _settle_terminal(
        self,
        current: RunRecord,
        status: str,
        live: List[Dict[str, Any]],
    ) -> RunRecord:
        """Cache a freshly-terminal run with TTL and result/error."""
        now = _now_iso()
        result_payload, error = _terminal_payload(status, live)
        expires_at = _expires_at_for(status, now)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE runs SET status = ?, result_json = ?, error = ?,
                       updated_at = ?, expires_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    (
                        json.dumps(result_payload)
                        if result_payload is not None
                        else None
                    ),
                    error,
                    now,
                    expires_at,
                    current.spec.run_id,
                ),
            )
        return RunRecord(
            spec=current.spec,
            status=status,
            result=result_payload,
            error=error,
            timestamps=Timestamps(
                created_at=current.timestamps.created_at,
                updated_at=now,
                expires_at=expires_at,
            ),
            task_ids=current.task_ids,
        )


def _build_list_where(
    owner: str, run_filter: RunFilter
) -> Tuple[str, List[Any]]:
    """Return the WHERE fragment + params for ``list_runs``."""
    clauses = ["user_id = ?"]
    params: List[Any] = [owner]
    for column, value in (
        ("status", run_filter.status),
        ("agent", run_filter.agent),
        ("origin", run_filter.origin),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    return " AND ".join(clauses), params


def _terminal_payload(
    status: str, live: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return the (result_payload, error) pair for a terminal run.

    Failed runs keep ``task_results`` alongside ``error`` so HTTP/MCP
    clients can show the failing task rows verbatim instead of only a
    one-line aggregate error string; the live ``task_results`` blob is
    the same shape ``reconcile_task`` returns per child task, so the
    failure detail stays self-describing.
    """
    if status == "succeeded":
        return {"task_results": live}, None
    failed = [
        row.get("task_id", "?")
        for row in live
        if (row.get("status") or "").lower() in _FAILURE_STATUSES
    ]
    return (
        {"task_results": live},
        f"one or more tasks failed: {', '.join(failed)}",
    )


def _row_to_record(
    run_id: str,
    row: Any,
    task_rows: List[Any],
) -> RunRecord:
    """Build a RunRecord from raw SELECT rows."""
    (
        user_id,
        agent,
        origin,
        status,
        result_json,
        error,
        created_at,
        updated_at,
        expires_at,
        dialogue_id,
        query,
        tool_name,
        model,
        request_json,
    ) = row
    return RunRecord(
        spec=RunSpec(
            run_id=run_id,
            user_id=user_id,
            agent=agent,
            origin=origin,
        ),
        status=status,
        result=json.loads(result_json) if result_json else None,
        error=error,
        timestamps=Timestamps(
            created_at=created_at,
            updated_at=updated_at,
            expires_at=expires_at,
        ),
        task_ids=tuple(t[0] for t in task_rows),
        request_info=RunRequestInfo(
            dialogue_id=dialogue_id,
            query=query,
            tool_name=tool_name,
            model=model,
            request_json=request_json,
        ),
    )


def _expires_at_for(status: str, now_iso: str) -> Optional[str]:
    """Compute the TTL ``expires_at`` for a terminal run status."""
    if status not in _TERMINAL_RUN_STATUSES:
        return None
    config = ApiConfig()
    base = datetime.fromisoformat(now_iso)
    delta = (
        timedelta(hours=config.API_RUN_TTL_OK_HOURS)
        if status == "succeeded"
        else timedelta(days=config.API_RUN_TTL_FAIL_DAYS)
    )
    return (base + delta).isoformat()
