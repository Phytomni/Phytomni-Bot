# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Task manager for SQLite database and remote task server interactions.

Classes: RunContext, Submission, TaskManager.
Functions: resolve_tasks_db_path.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from ..config.defaults import ApiConfig


@dataclass(frozen=True)
class RunContext:
    """Run-scope context passed to ``TaskManager.record_submission``.

    All fields default to ``None`` so an old positional call writes NULL
    into the run-scoped columns (byte-equivalent to the original
    4-column behavior), while the unified run-registry chokepoint
    supplies them.

    Attributes:
        run_id: Owning run id (``IdFactory().new_id("run", agent)``).
        user_id: Authenticated user (``"anonymous"`` on the MCP path).
        agent: Public agent alias (e.g. ``"analyst"``).
        origin: ``"remote"`` for analysis-platform submissions,
            ``"local"`` for in-process synchronous runs.
        created_at: ISO-8601 row creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
    """

    run_id: Optional[str] = None
    user_id: Optional[str] = None
    agent: Optional[str] = None
    origin: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class Submission:
    """A full task-row write spec for ``TaskManager.record``.

    Bundles the task-grain fields with an optional ``RunContext`` so
    ``record`` takes a single ``submission`` argument (which keeps the
    method pylint-clean at the project's two-positional-argument limit
    while still expressing every column the table now carries).

    Attributes:
        task_id: The MCP-facing task id returned to the caller.
        status: Submission status to record (e.g. ``"submitted"``).
        output_dir: Output directory reported by the submission.
        analysis_id: Optional remote/analysis-platform id used by the
            live status bridge; empty when not yet known.
        run_context: Optional run-scope context for the unified run
            registry; ``None`` keeps the run-scoped columns ``NULL``.
        input_fingerprint: Optional deterministic identity digest for
            the submission inputs. When supplied, downstream callers
            can look the row up via
            ``TaskManager.get_task_by_fingerprint`` to short-circuit a
            duplicate long-running submission and reuse the prior
            remote ``task_id`` instead of launching a fresh job.
        source_task_id: Optional cross-tenant pointer to the prior
            tenant's remote task id used for live-status probing on a
            content-addressed dedup hit. Written once at mint time and
            never overwritten by a later write that omits it
            (``COALESCE`` guard in the upsert).
    """

    task_id: str
    status: str
    output_dir: str
    analysis_id: str = ""
    run_context: Optional[RunContext] = None
    input_fingerprint: Optional[str] = None
    source_task_id: Optional[str] = None


# Fresh-database schema: ``CREATE TABLE IF NOT EXISTS`` creates all
# columns in one shot. The original 4 columns stay first for
# backward compatibility with pre-existing rows.
_CREATE_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT,
    analysis_id TEXT,
    output_dir TEXT,
    run_id TEXT,
    user_id TEXT,
    agent TEXT,
    origin TEXT,
    created_at TEXT,
    updated_at TEXT,
    input_fingerprint TEXT,
    task_log TEXT,
    final_report TEXT,
    degraded_reason TEXT,
    source_task_id TEXT
)
"""

# In-place migration for legacy 4-column databases. SQLite has no
# ``ADD COLUMN IF NOT EXISTS``, so each ALTER is guarded by a
# ``PRAGMA table_info(tasks)`` lookup at call time; statements are
# pre-baked literals (no SQL identifier interpolation).
_TASK_ADD_COLUMN_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("run_id", "ALTER TABLE tasks ADD COLUMN run_id TEXT"),
    ("user_id", "ALTER TABLE tasks ADD COLUMN user_id TEXT"),
    ("agent", "ALTER TABLE tasks ADD COLUMN agent TEXT"),
    ("origin", "ALTER TABLE tasks ADD COLUMN origin TEXT"),
    ("created_at", "ALTER TABLE tasks ADD COLUMN created_at TEXT"),
    ("updated_at", "ALTER TABLE tasks ADD COLUMN updated_at TEXT"),
    (
        "input_fingerprint",
        "ALTER TABLE tasks ADD COLUMN input_fingerprint TEXT",
    ),
    ("task_log", "ALTER TABLE tasks ADD COLUMN task_log TEXT"),
    ("final_report", "ALTER TABLE tasks ADD COLUMN final_report TEXT"),
    (
        "degraded_reason",
        "ALTER TABLE tasks ADD COLUMN degraded_reason TEXT",
    ),
    (
        "source_task_id",
        "ALTER TABLE tasks ADD COLUMN source_task_id TEXT",
    ),
)

# Status values that disqualify a prior row from being reused via
# ``get_task_by_fingerprint``: the analyst remote platform reports
# ``"failed"`` / ``"error"`` / ``"cancelled"`` on terminal failures,
# and the in-process LangGraph leg raises
# ``"failed_at_agent_level"``. A failed prior must trigger a fresh
# submission rather than handing the caller a dead remote id.
_DEAD_TASK_STATUSES: tuple[str, ...] = (
    "failed",
    "error",
    "cancelled",
    "failed_at_agent_level",
)


class TaskManager:
    """Manages tasks in a SQLite database.

    This class provides methods to initialize a database, create new tasks,
    and update existing tasks.

    Attributes:
        db_path (str): The path to the SQLite database file.
    """

    def __init__(self, db_path="server_tasks.db"):
        """Initializes the TaskManager with the given database path.

        Args:
            db_path (str, optional): The path to the SQLite database file.
                                     Defaults to 'server_tasks.db'.
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Create or in-place widen the ``tasks`` table.

        Fresh databases receive every column from ``_CREATE_TASKS_DDL``.
        Legacy 4-column databases are upgraded in place by guarded
        ``ALTER TABLE ADD COLUMN`` (SQLite has no ``IF NOT EXISTS``
        column form); pre-existing rows get ``NULL`` for the new
        columns automatically.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_CREATE_TASKS_DDL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        for column, statement in _TASK_ADD_COLUMN_STATEMENTS:
            if column not in existing:
                conn.execute(statement)
        conn.commit()
        conn.close()

    def _get_connection(self):
        """Returns a connection to the SQLite database.

        Returns:
            sqlite3.Connection: SQLite database connection.
        """
        return sqlite3.connect(self.db_path)

    def create_task(self):
        """Creates a new task with a unique ID and initial status.

        Returns:
            str: The ID of the newly created task.
        """
        task_id = str(uuid.uuid4())
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO tasks (task_id, status, analysis_id, output_dir)
            VALUES (?, ?, ?, ?)
        """,
            (task_id, "running", "unupdated", "unupdated"),
        )
        conn.commit()
        conn.close()
        return task_id

    def update_task(self, task_id, status, analysis_id, output_dir):
        """Updates the status, analysis_id, and output_dir of a task.

        Args:
            task_id (str): The ID of the task to update.
            status (str): The new status of the task.
            analysis_id (str): The new analysis ID of the task.
            output_dir (str): The new output directory of the task.
        """
        conn = self._get_connection()
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, analysis_id = ?, output_dir = ? WHERE task_id = ?
        """,
            (status, analysis_id, output_dir, task_id),
        )
        conn.commit()
        conn.close()

    def record(self, submission: Submission) -> None:
        """Upsert one task row from a ``Submission`` spec.

        Inserts the row, or on a ``task_id`` conflict updates every
        run-scoped column from the new write while preserving the
        existing ``input_fingerprint`` when the new write omits one
        (``COALESCE``). The dispatch seam writes the fingerprint row
        first with ``NULL`` run columns; the per-tool recorder later
        writes the same ``task_id`` with run linkage but no fingerprint,
        and the ``COALESCE`` stops that second write from erasing the
        dedup key (the old ``INSERT OR REPLACE`` deleted and reinserted
        the row, nulling it). A submission without a ``RunContext``
        still leaves the run-scoped columns ``NULL``; the unified
        run-registry chokepoint passes a populated context so a run's
        child tasks share ``run_id`` / ``user_id`` / ``agent`` /
        ``origin`` / timestamps.

        Args:
            submission: The full per-row write spec.
        """
        ctx = submission.run_context or RunContext()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, status, analysis_id, output_dir,
                run_id, user_id, agent, origin, created_at, updated_at,
                input_fingerprint, source_task_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = excluded.status,
                analysis_id = excluded.analysis_id,
                output_dir = excluded.output_dir,
                run_id = excluded.run_id,
                user_id = excluded.user_id,
                agent = excluded.agent,
                origin = excluded.origin,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                input_fingerprint = COALESCE(
                    excluded.input_fingerprint, tasks.input_fingerprint
                ),
                source_task_id = COALESCE(
                    excluded.source_task_id, tasks.source_task_id
                )
        """,
            (
                submission.task_id,
                submission.status,
                submission.analysis_id,
                submission.output_dir,
                ctx.run_id,
                ctx.user_id,
                ctx.agent,
                ctx.origin,
                ctx.created_at,
                ctx.updated_at,
                submission.input_fingerprint,
                submission.source_task_id,
            ),
        )
        conn.commit()
        conn.close()

    def get_task_by_fingerprint(
        self, input_fingerprint: str
    ) -> Optional[Dict[str, str]]:
        """Return the most-recent non-failed task row for a fingerprint.

        Filters out terminal-failed rows (``_DEAD_TASK_STATUSES``) so a
        prior failed attempt never short-circuits a fresh submission;
        in-flight (``submitted`` / ``running`` / ``pending``) and
        succeeded rows are eligible for reuse. ``ORDER BY rowid DESC``
        picks the most recent surviving row when multiple rows share a
        fingerprint (e.g., a re-submitted task after an earlier failed
        attempt).

        Args:
            input_fingerprint: Deterministic identity digest produced
                by the caller (e.g. ``analyst_task_fingerprint``).

        Returns:
            ``{"task_id", "status", "analysis_id", "output_dir",
            "source_task_id"}`` for a reusable prior row
            (``source_task_id`` holds the original remote id on a
            dedup-reuse row, else ``None``), or ``None`` when no
            non-failed match exists.
        """
        placeholders = ",".join("?" for _ in _DEAD_TASK_STATUSES)
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                f"""
                SELECT task_id, status, analysis_id, output_dir,
                       source_task_id
                FROM tasks
                WHERE input_fingerprint = ?
                  AND (
                      status IS NULL
                      OR lower(status) NOT IN ({placeholders})
                  )
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (input_fingerprint, *_DEAD_TASK_STATUSES),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "task_id": row[0],
            "status": row[1],
            "analysis_id": row[2],
            "output_dir": row[3],
            "source_task_id": row[4],
        }

    def record_submission(
        self,
        task_id: str,
        status: str,
        output_dir: str,
        analysis_id: str = "",
    ) -> None:
        """Upsert a submitted task's row by its known task_id.

        Backward-compatible shim preserved for existing callers
        (handlers' submit chokepoint and the older unit tests) so they
        continue to work byte-for-byte with a 4-column-style write.
        New callers should use ``record(Submission(...))`` directly to
        populate the run-scoped columns.

        Args:
            task_id: The MCP-facing task id returned to the caller.
            status: Submission status to record (e.g. ``"submitted"``).
            output_dir: Output directory reported by the submission.
            analysis_id: Optional remote/analysis-platform id used by
                the live status bridge; empty when not yet known.
        """
        self.record(
            Submission(
                task_id=task_id,
                status=status,
                output_dir=output_dir,
                analysis_id=analysis_id,
            )
        )

    def get_task(self, task_id: str) -> Optional[Dict[str, str]]:
        """Return one task row, or None when the id is unknown.

        A single non-blocking ``SELECT`` — never polls or waits — so
        callers (the GetTaskStatus tool) cannot re-create the C-1
        MCP-timeout problem.

        Args:
            task_id: The task id to look up.

        Returns:
            ``{"task_id", "status", "analysis_id", "output_dir",
            "source_task_id"}`` when the row exists, otherwise ``None``.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT status, analysis_id, output_dir, source_task_id
                FROM tasks WHERE task_id = ?
            """,
                (task_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "task_id": task_id,
            "status": row[0],
            "analysis_id": row[1],
            "output_dir": row[2],
            "source_task_id": row[3],
        }

    def get_task_agent(self, task_id: str) -> Optional[str]:
        """Return the recorded ``agent`` tag for ``task_id`` (or None).

        Kept separate from ``get_task`` so the tool-facing ``get_task``
        response shape stays narrow; the widened run-scoped columns must
        not leak into the GetTaskStatus surface. ``reconcile_task`` reads
        the agent through this focused accessor to tell a local
        deep_genome umbrella row from a remote child sub-task.

        Args:
            task_id: The task id to look up.

        Returns:
            The ``agent`` column value, or ``None`` when the row is
            missing or its agent is unset.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT agent FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        return row[0] if row is not None else None

    def set_task_log(self, task_id: str, log_dict: dict) -> bool:
        """Serialize a dict to JSON and write to the task_log column.

        The /v1/runs/{run_id}/logs endpoint caches remote step logs
        locally to avoid polling the analysis platform on every refresh.
        This helper provides a single atomic write to the task_log TEXT
        column; get_task_log reads it back.

        Args:
            task_id: The task id to update.
            log_dict: The log payload to serialize and persist.

        Returns:
            True if a row was updated, False if the task_id is unknown.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE tasks SET task_log = ? WHERE task_id = ?",
                (json.dumps(log_dict, ensure_ascii=False), task_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_task_log(self, task_id: str) -> Optional[dict]:
        """Read the task_log column and deserialize from JSON.

        Returns the cached log dict for a task, or None if the task
        does not exist or the task_log column is NULL. A non-blocking
        single SELECT — no polling or waiting.

        Args:
            task_id: The task id to look up.

        Returns:
            The log dict if present, otherwise None.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT task_log FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return json.loads(row[0])

    def set_task_final_report(self, task_id: str, markdown: str) -> bool:
        """Write the assembled report markdown to the final_report column.

        DeepGenome runs its whole report workflow in the background and
        writes the finished markdown to the local row so the non-blocking
        poll path (GetTaskStatus / run-aggregate) can surface it without
        re-running the workflow. A single targeted ``UPDATE`` (never the
        ``INSERT OR REPLACE`` ``record`` path) so a later ``update_task``
        status flip — which SETs only status / analysis_id / output_dir —
        cannot wipe the report.

        Args:
            task_id: The task id to update.
            markdown: The assembled report markdown to persist verbatim.

        Returns:
            True if a row was updated, False if the task_id is unknown.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE tasks SET final_report = ? WHERE task_id = ?",
                (markdown, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_task_final_report(self, task_id: str) -> Optional[str]:
        """Read the final_report column as a markdown string.

        Returns the persisted report for a task, or None when the task
        does not exist or the column is NULL (every non-DeepGenome task
        and any row created before the column shipped). A non-blocking
        single SELECT — no polling or waiting.

        Args:
            task_id: The task id to look up.

        Returns:
            The report markdown if present, otherwise None.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT final_report FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return row[0]

    def set_task_degraded(self, task_id: str, reason: str) -> bool:
        """Persist a redacted degraded reason on the task row.

        DeepGenome runs its report workflow in the background; when the
        brief_gene mount degrades, the final report node writes the
        redacted reason here so the non-blocking poll path
        (GetTaskStatus / run-aggregate) can surface ``degraded`` without
        re-running the workflow. A single targeted ``UPDATE`` (never the
        ``record`` upsert) so a later ``update_task`` status flip cannot
        wipe it. The caller redacts before persisting so the stored
        string is already client-safe.

        Args:
            task_id: The task id to update.
            reason: Redacted, client-safe degradation reason.

        Returns:
            True if a row was updated, False if the task_id is unknown.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE tasks SET degraded_reason = ? WHERE task_id = ?",
                (reason, task_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_task_degraded(self, task_id: str) -> Optional[str]:
        """Read the degraded reason, or None when the run is healthy.

        Returns the persisted redacted reason for a degraded task, or
        None when the task does not exist or the column is NULL (every
        healthy run and every non-DeepGenome task). A non-blocking single
        SELECT — no polling or waiting.

        Args:
            task_id: The task id to look up.

        Returns:
            The redacted reason string if degraded, otherwise None.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT degraded_reason FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return row[0]


def resolve_tasks_db_path() -> str:
    """Return the shared SQLite path for the local task registry.

    Single source of truth so the submit-side writer and the
    GetTaskStatus reader provably address the same file. Reads
    ``ApiConfig.API_TASKS_DB_PATH`` (env ``API_TASKS_DB_PATH`` /
    ``PHYTOMNI_TASKS_DB``, default ``server_tasks.db``).

    Returns:
        The configured tasks database path.
    """
    return ApiConfig().API_TASKS_DB_PATH
