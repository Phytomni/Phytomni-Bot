# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Task manager for SQLite database and remote task server interactions.

Classes: RemoteTaskRequest, RunContext, Submission, TaskManager.
Functions: create_task (async), update_task (async),
    resolve_tasks_db_path.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from httpx import Timeout

from ..common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ..common.httpx_client import get_async_client
from ..common.relay_client import current_relay_client
from ..config.defaults import ApiConfig
from ..config.relay_mode import relay_mode_enabled

DEFAULT_RETRIABLE_CODES = (429, 500, 502, 503, 504)

# Per-request timeout (seconds) for the remote task-manager create/update
# calls when the caller passes no explicit ``timeout=`` kwarg. Lives at
# module scope so the value is observable to tests / operators and shares
# one source of truth between ``create_task`` and ``update_task`` instead
# of repeating the magic number at each call site.
DEFAULT_REMOTE_TASK_TIMEOUT = 60


@dataclass(frozen=True)
class RemoteTaskRequest:
    """Resolved request data for a remote task-manager call.

    Attributes:
        url: Target URL for the remote task request.
        data: Dictionary of task data fields.
        timeout: Request timeout in seconds.
        retriable_codes: Tuple of HTTP status codes that trigger retry.
        max_retries: Maximum number of retry attempts.
        message: Error message prefix for failures.
        relay_path: Relay route suffix (``task/create`` / ``task/update``)
            used instead of ``url`` in customer relay mode.
    """

    url: str
    data: Dict[str, str]
    timeout: float
    retriable_codes: tuple[int, ...]
    max_retries: int
    message: str
    relay_path: str


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
# columns in one shot. The original 4 columns stay first so old code
# paths (``create_task``/``update_task``) keep working unchanged.
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
            ``{"task_id", "status", "analysis_id", "output_dir"}`` for a
            reusable prior row, or ``None`` when no non-failed match
            exists.
        """
        placeholders = ",".join("?" for _ in _DEAD_TASK_STATUSES)
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                f"""
                SELECT task_id, status, analysis_id, output_dir
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


async def create_task(
    url,
    **kwargs: Any,
):
    """Creates a task on a remote server.

    This function sends a POST request to the specified URL to create a new
    task. It implements a retry mechanism with exponential backoff for
    transient errors.

    Args:
        url (str): The URL of the remote server.
        server_id (str): The ID of the server.
        server_status (str): The status of the server.
        tool_name (str): The name of the tool being used.
        timeout (int, optional): The timeout for the request in seconds.
                                 Defaults to 60.
        retriable_codes (List[int], optional): A list of HTTP status codes
                                               that trigger a retry.
                                               Defaults to
                                               [429, 500, 502, 503, 504].
        max_retries (int, optional): The maximum number of retries.
                                     Defaults to 5.

    Returns:
        dict: The JSON response from the server.

    Raises:
        McpError: If the request fails after all retries.
    """
    request = RemoteTaskRequest(
        url=url,
        data={
            "server_id": kwargs["server_id"],
            "server_status": kwargs["server_status"],
            "tool_name": kwargs["tool_name"],
        },
        timeout=kwargs.get("timeout", DEFAULT_REMOTE_TASK_TIMEOUT),
        retriable_codes=_retriable_codes(kwargs.get("retriable_codes")),
        max_retries=kwargs.get("max_retries", 5),
        message="Failed to create task",
        relay_path="task/create",
    )
    return await _post_remote_task(request)


async def update_task(
    url,
    **kwargs: Any,
):
    """Updates a task on a remote server.

    This function sends a POST request to the specified URL to update an
    existing task. It implements a retry mechanism with exponential backoff
    for transient errors.

    Args:
        url (str): The URL of the remote server.
        server_id (str): The ID of the server.
        server_status (str): The status of the server.
        server_file_path (str): The path to the file on the server.
        tool_result (str): The result of the tool execution.
        timeout (int, optional): The timeout for the request in seconds.
                                 Defaults to 60.
        retriable_codes (List[int], optional): A list of HTTP status codes
                                               that trigger a retry.
                                               Defaults to
                                               [429, 500, 502, 503, 504].
        max_retries (int, optional): The maximum number of retries.
                                     Defaults to 5.

    Returns:
        dict: The JSON response from the server.

    Raises:
        McpError: If the request fails after all retries.
    """
    request = RemoteTaskRequest(
        url=url,
        data={
            "server_id": kwargs["server_id"],
            "server_status": kwargs["server_status"],
            "server_file_path": kwargs["server_file_path"],
            "tool_result": kwargs["tool_result"],
        },
        timeout=kwargs.get("timeout", DEFAULT_REMOTE_TASK_TIMEOUT),
        retriable_codes=_retriable_codes(kwargs.get("retriable_codes")),
        max_retries=kwargs.get("max_retries", 5),
        message="Failed to update task",
        relay_path="task/update",
    )
    return await _post_remote_task(request)


def _retriable_codes(value: Any) -> tuple[int, ...]:
    """Return retryable status codes from an override or defaults."""
    if value is None:
        return DEFAULT_RETRIABLE_CODES
    return tuple(value)


async def _post_remote_task(request: RemoteTaskRequest):
    """Post one remote task-manager request with retry handling.

    The local ``server_tasks.db`` registry stays local; only this remote
    POST path is relayed. In customer relay mode the form body is sent to
    ``/v1/relay/task/{create,update}`` (an unauthenticated upstream the
    relay forwards verbatim) instead of the operator task URL.
    """
    if relay_mode_enabled():
        return await current_relay_client().post_data(
            request.relay_path, data=request.data, message=request.message
        )
    client_timeout = Timeout(request.timeout, connect=request.timeout)
    async with get_async_client(timeout=client_timeout) as client:
        return await post_json_with_retries(
            client,
            JsonPostRequest(url=request.url, data=request.data),
            JsonPostRetry(
                timeout=request.timeout,
                max_retries=request.max_retries,
                retriable_codes=request.retriable_codes,
                message=request.message,
            ),
        )
