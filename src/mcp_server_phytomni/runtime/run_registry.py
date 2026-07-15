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

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .task_manager import (
    TaskManager,
    _expires_at_for,
    resolve_tasks_db_path,
)
from .task_reconcile import reconcile_task
from .terminal_answer import TerminalAnswerContext, synthesize_terminal_answer
from .terminal_artifacts import (
    ArtifactLister,
    collect_terminal_artifacts,
    enumerate_artifact_paths,
)
from .terminal_report import (
    TerminalReportContext,
    is_terminal_report_agent,
    persist_terminal_report,
    synthesize_terminal_report,
)

__all__ = [
    "A2ACorrelation",
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
_NON_POLLABLE_RUN_STATUSES = _TERMINAL_RUN_STATUSES | {"input_required"}

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
    request_json TEXT,
    a2a_task_id TEXT,
    a2a_context_id TEXT,
    a2a_message_id TEXT
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

_A2A_COLUMNS = (
    ("a2a_task_id", "TEXT"),
    ("a2a_context_id", "TEXT"),
    ("a2a_message_id", "TEXT"),
)

_CREATE_RUNS_USER_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)"
)
_CREATE_TASKS_RUN_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id)"
)
_CREATE_A2A_TASK_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_runs_a2a_task_user "
    "ON runs(a2a_task_id, user_id)"
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _aggregate_status(task_statuses: list[str]) -> str:
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
class A2ACorrelation:
    """Protocol ids linking one A2A task to an existing run row."""

    task_id: str | None = None
    context_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class RunOutcome:
    """Initial outcome state of a newly-created run row.

    Attributes:
        status: Run status string; ``"running"`` for in-flight,
            ``"succeeded"``/``"failed"`` for terminal-on-creation
            sync runs.
        result: Terminal result payload (sync runs only); JSON-encoded
            into the ``result_json`` column.
        error: Terminal error message (failed sync runs only).
    """

    status: str = "running"
    result: dict[str, Any] | None = None
    error: str | None = None


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
        a2a: Protocol ids when the request came through the A2A facade.
    """

    dialogue_id: str | None = None
    query: str | None = None
    tool_name: str | None = None
    model: str | None = None
    request_json: str | None = None
    a2a: A2ACorrelation = A2ACorrelation()


@dataclass(frozen=True)
class RunFilter:
    """Optional list-time filters for ``list_runs``.

    Attributes:
        status: Exact-match run status filter.
        agent: Exact-match agent alias filter.
        origin: Exact-match origin filter (``"local"``/``"remote"``).
        dialogue_id: Exact-match dialogue id filter. Chat-ai groups
            its history page by ``dialogue_id``; the server-side
            predicate runs before ``limit`` / ``offset`` so an owner
            with more than ``limit`` rows still finds their target
            dialogue regardless of ordering.
        created_after: ISO-8601 lower bound (inclusive); rows with
            ``created_at >= created_after`` are kept. Both bounds are
            compared as TEXT directly: ISO-8601 timestamps are
            lexicographically ordered when normalised to UTC + the same
            offset form, which matches every ``_now_iso()`` write here.
        created_before: ISO-8601 upper bound (inclusive); rows with
            ``created_at <= created_before`` are kept.
    """

    status: str | None = None
    agent: str | None = None
    origin: str | None = None
    dialogue_id: str | None = None
    created_after: str | None = None
    created_before: str | None = None


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
    expires_at: str | None


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
    result: dict[str, Any] | None
    error: str | None
    timestamps: Timestamps
    task_ids: tuple[str, ...]
    request_info: RunRequestInfo = RunRequestInfo()

    @property
    def a2a(self) -> A2ACorrelation:
        """Return the protocol correlation nested in request metadata."""
        return self.request_info.a2a


class RunRegistry:
    """Run-level CRUD + aggregation over the shared task database.

    Shares ``resolve_tasks_db_path`` with ``TaskManager`` so a run row
    and its child tasks are always co-located in one SQLite file.
    """

    def __init__(self, db_path: str | None = None) -> None:
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
            for column, column_type in (*_REQUEST_INFO_COLUMNS, *_A2A_COLUMNS):
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(
                        f"ALTER TABLE runs ADD COLUMN {column} {column_type}"
                    )
            conn.execute(_CREATE_RUNS_USER_INDEX)
            conn.execute(_CREATE_TASKS_RUN_INDEX)
            conn.execute(_CREATE_A2A_TASK_INDEX)
            conn.commit()
        finally:
            conn.close()

    def create_run(
        self,
        spec: RunSpec,
        *,
        outcome: RunOutcome | None = None,
        request_info: RunRequestInfo | None = None,
        a2a: A2ACorrelation | None = None,
    ) -> None:
        """Insert a new run row.

        Idempotent via ``INSERT OR REPLACE`` so a retried chokepoint
        write does not crash on the duplicate run_id key. The expiry is
        set immediately for terminal-on-creation runs (sync agents)
        using the OK / FAIL TTL knobs from ``ApiConfig``.

        Args:
            spec: Identity bundle (run_id, user_id, agent, origin).
            outcome: Initial status + terminal result/error bundle;
                defaults to a fresh ``RunOutcome()`` (status="running",
                no result, no error). Sync agents that finish on
                creation pass an explicit ``RunOutcome(status=...,
                result=...)`` or ``RunOutcome(status=..., error=...)``.
            request_info: Per-request metadata captured at the HTTP
                boundary; ``None`` (default) leaves every column NULL
                so MCP-path runs that never see request context
                continue to write the same five fields as before.
        """
        outcome = outcome or RunOutcome()
        status = outcome.status
        result = outcome.result
        error = outcome.error
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        info = request_info or RunRequestInfo()
        a2a_info = a2a or A2ACorrelation()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, user_id, agent, origin, status,
                    result_json, error, created_at, updated_at,
                    expires_at,
                    dialogue_id, query, tool_name, model, request_json,
                    a2a_task_id, a2a_context_id, a2a_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    a2a_info.task_id,
                    a2a_info.context_id,
                    a2a_info.message_id,
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

    def settle_run(
        self,
        run_id: str,
        *,
        owner: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Settle an owned run to a terminal status in place.

        Unlike ``create_run``'s INSERT OR REPLACE, this is a targeted
        UPDATE that preserves ``created_at`` (and every request-info
        column), mirroring the ``_settle_terminal`` idiom used by the
        reconcile path. The terminal TTL is stamped the same way.

        Args:
            run_id: Run id to settle.
            owner: Required user id; a foreign-owned row is a no-op.
            status: Terminal status ("succeeded" / "failed").
            result: Optional terminal result payload (JSON-encoded).
            error: Optional terminal error message.

        Returns:
            True when an owned row was updated, False otherwise.
        """
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    status = ?,
                    result_json = ?,
                    error = ?,
                    updated_at = ?,
                    expires_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    now,
                    expires_at,
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def get_run(self, run_id: str, *, owner: str) -> RunRecord | None:
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
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, query, tool_name, model, request_json,
                       a2a_task_id, a2a_context_id, a2a_message_id
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
        return _row_to_record(row, task_rows)

    def update_a2a_correlation(
        self,
        run_id: str,
        *,
        owner: str,
        correlation: A2ACorrelation,
    ) -> bool:
        """Attach or replace A2A ids on an owned run row.

        The update is additive and owner-scoped. It preserves the run's
        status/result and is safe to repeat when a client retries the same
        A2A request.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    a2a_task_id = ?,
                    a2a_context_id = ?,
                    a2a_message_id = ?,
                    updated_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    correlation.task_id,
                    correlation.context_id,
                    correlation.message_id,
                    _now_iso(),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def get_run_by_a2a_task(
        self,
        task_id: str,
        *,
        owner: str,
    ) -> RunRecord | None:
        """Return the owned run projected by an A2A task id."""
        if not task_id:
            return None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, query, tool_name, model, request_json,
                       a2a_task_id, a2a_context_id, a2a_message_id
                FROM runs WHERE a2a_task_id = ? AND user_id = ?
                ORDER BY updated_at DESC, run_id DESC LIMIT 1
                """,
                (task_id, owner),
            ).fetchone()
            if row is None:
                return None
            task_rows = conn.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? "
                "ORDER BY task_id",
                (row["run_id"],),
            ).fetchall()
        return _row_to_record(row, task_rows)

    def list_runs(
        self,
        *,
        owner: str,
        run_filter: RunFilter | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[RunRecord]:
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
        records: list[RunRecord] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT run_id, user_id, agent, origin, status,
                       result_json, error, created_at, updated_at,
                       expires_at,
                       dialogue_id, query, tool_name, model, request_json,
                       a2a_task_id, a2a_context_id, a2a_message_id
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
                    (run_row["run_id"],),
                ).fetchall()
                records.append(_row_to_record(run_row, task_rows))
        return records

    async def reconcile(
        self,
        run_id: str,
        *,
        owner: str,
        lister: ArtifactLister | None = None,
    ) -> RunRecord | None:
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
        if current is None or current.status in _NON_POLLABLE_RUN_STATUSES:
            return current
        live: list[dict[str, Any]] = []
        for task_id in current.task_ids:
            live.append(await reconcile_task(task_id))
        new_status = _aggregate_status([row["status"] for row in live])
        if new_status not in _TERMINAL_RUN_STATUSES:
            return self._touch_running(current, new_status)
        if new_status == "succeeded":
            live = await enumerate_artifact_paths(live, lister=lister)
        artifacts = (
            collect_terminal_artifacts(live)
            if new_status == "succeeded"
            else []
        )
        report_result = None
        if new_status == "succeeded" and is_terminal_report_agent(
            current.spec.agent
        ):
            report_result = await synthesize_terminal_report(
                TerminalReportContext(
                    agent=current.spec.agent,
                    status=new_status,
                    live=live,
                    artifacts=artifacts,
                    query=current.request_info.query,
                )
            )
            persist_terminal_report(live, report_result)
        answer = await synthesize_terminal_answer(
            TerminalAnswerContext(
                agent=current.spec.agent,
                status=new_status,
                live=live,
                artifacts=artifacts,
                query=current.request_info.query,
            )
        )
        if report_result is not None and report_result.answer:
            answer = report_result.answer
        result_payload, error = _terminal_payload(
            new_status, live, artifacts, answer
        )
        return self._settle_terminal(
            current, new_status, result_payload, error
        )

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
        result_payload: dict[str, Any] | None,
        error: str | None,
    ) -> RunRecord:
        """Cache a freshly-terminal run with TTL and result/error."""
        now = _now_iso()
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
) -> tuple[str, list[Any]]:
    """Return the WHERE fragment + params for ``list_runs``."""
    clauses = ["user_id = ?"]
    params: list[Any] = [owner]
    for column, value in (
        ("status", run_filter.status),
        ("agent", run_filter.agent),
        ("origin", run_filter.origin),
        ("dialogue_id", run_filter.dialogue_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if run_filter.created_after is not None:
        clauses.append("created_at >= ?")
        params.append(run_filter.created_after)
    if run_filter.created_before is not None:
        clauses.append("created_at <= ?")
        params.append(run_filter.created_before)
    return " AND ".join(clauses), params


def _terminal_payload(
    status: str,
    live: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    answer: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the (result_payload, error) pair for a terminal run.

    Both branches ship the same structured blocks so clients polling
    /v1/runs/{id} get a uniform shape regardless of terminal direction:

    - ``task_results``: the reconciled task rows (the existing field).
    - ``live_status``: the same reconciled rows surfaced under a stable
      "raw live blob" namespace.
    - ``artifacts``: the caller-supplied succeeded-task product index
      (empty on the failed branch); always present so clients can
      iterate it without a key-check.
    - ``formatted.answer``: the caller-synthesized renderable answer,
      always present when the caller provides one so clients see a
      compact display surface alongside the long-form ``final_report``.
    - ``degraded``: True when any reconciled child carries a degraded
      signal (e.g. a DeepGenome report that lost its gene profile); the
      per-task ``degraded_reason`` rides ``task_results``.
    """
    payload: dict[str, Any] = {
        "task_results": live,
        "live_status": live,
        "artifacts": artifacts,
        "final_report": _first_final_report(live),
        "degraded": _any_degraded(live),
    }
    if answer:
        payload["formatted"] = {"answer": answer}
    if status == "succeeded":
        return payload, None
    failed = [
        row.get("task_id", "?")
        for row in live
        if (row.get("status") or "").lower() in _FAILURE_STATUSES
    ]
    return payload, f"one or more tasks failed: {', '.join(failed)}"


def _first_final_report(live: list[dict[str, Any]]) -> str | None:
    """Return the first non-empty child ``final_report``, else None.

    DeepGenome's single umbrella child persists the assembled report on
    its reconciled row; other agents leave it absent. Surfacing the
    first non-empty value lets /v1/runs/{id} expose the report at the
    payload top level without the client walking ``task_results``.
    """
    for row in live:
        report = row.get("final_report")
        if isinstance(report, str) and report:
            return report
    return None


def _any_degraded(live: list[dict[str, Any]]) -> bool:
    """Return True when any reconciled child task is degraded.

    DeepGenome flags optional analysis or literature degradation on its
    reconciled row; rolling the flag up to the run aggregate lets a client
    polling /v1/runs/{id} learn a child degraded without walking
    ``task_results`` (which still carries the per-task ``degraded_reason``).
    """
    return any(bool(row.get("degraded")) for row in live)


def _row_to_record(
    row: sqlite3.Row,
    task_rows: list[Any],
) -> RunRecord:
    """Build a RunRecord from a ``sqlite3.Row`` of the ``runs`` table.

    The row must include the 15 columns listed in get_run / list_runs
    SELECTs; column-name access keeps this helper readable without
    a 14-line unpacking block.
    """
    result_json = row["result_json"]
    return RunRecord(
        spec=RunSpec(
            run_id=row["run_id"],
            user_id=row["user_id"],
            agent=row["agent"],
            origin=row["origin"],
        ),
        status=row["status"],
        result=json.loads(result_json) if result_json else None,
        error=row["error"],
        timestamps=Timestamps(
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        ),
        task_ids=tuple(t[0] for t in task_rows),
        request_info=RunRequestInfo(
            dialogue_id=row["dialogue_id"],
            query=row["query"],
            tool_name=row["tool_name"],
            model=row["model"],
            request_json=row["request_json"],
            a2a=A2ACorrelation(
                task_id=row["a2a_task_id"],
                context_id=row["a2a_context_id"],
                message_id=row["a2a_message_id"],
            ),
        ),
    )
