# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Task manager for SQLite database and remote task server interactions.

Classes: RemoteTaskRequest, TaskManager.
Functions: create_task (async), update_task (async),
    resolve_tasks_db_path.
"""

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from httpx import (
    AsyncClient,
    Timeout,
)

from ..common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from ..config.defaults import ApiConfig

DEFAULT_RETRIABLE_CODES = (429, 500, 502, 503, 504)


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
    """

    url: str
    data: Dict[str, str]
    timeout: float
    retriable_codes: tuple[int, ...]
    max_retries: int
    message: str


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
        """Initializes the database and creates the tasks
        table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                analysis_id TEXT,
                output_dir TEXT
            )
        """)
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

    def record_submission(
        self,
        task_id: str,
        status: str,
        output_dir: str,
        analysis_id: str = "",
    ) -> None:
        """Upsert a submitted task's row by its known task_id.

        Unlike ``create_task`` (which mints its own uuid), the submit
        chokepoint already holds the MCP-facing ``task_id``, so this
        writes that exact row idempotently (``INSERT OR REPLACE``).

        Args:
            task_id: The MCP-facing task id returned to the caller.
            status: Submission status to record (e.g. ``"submitted"``).
            output_dir: Output directory reported by the submission.
            analysis_id: Optional remote/analysis-platform id used by
                the live status bridge; empty when not yet known.
        """
        conn = self._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks
                (task_id, status, analysis_id, output_dir)
            VALUES (?, ?, ?, ?)
        """,
            (task_id, status, analysis_id, output_dir),
        )
        conn.commit()
        conn.close()

    def get_task(self, task_id: str) -> Optional[Dict[str, str]]:
        """Return one task row, or None when the id is unknown.

        A single non-blocking ``SELECT`` — never polls or waits — so
        callers (the GetTaskStatus tool) cannot re-create the C-1
        MCP-timeout problem.

        Args:
            task_id: The task id to look up.

        Returns:
            ``{"task_id", "status", "analysis_id", "output_dir"}`` when
            the row exists, otherwise ``None``.
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT status, analysis_id, output_dir
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
        }


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
        timeout=kwargs.get("timeout", 60),
        retriable_codes=_retriable_codes(kwargs.get("retriable_codes")),
        max_retries=kwargs.get("max_retries", 5),
        message="Failed to create task",
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
        timeout=kwargs.get("timeout", 60),
        retriable_codes=_retriable_codes(kwargs.get("retriable_codes")),
        max_retries=kwargs.get("max_retries", 5),
        message="Failed to update task",
    )
    return await _post_remote_task(request)


def _retriable_codes(value: Any) -> tuple[int, ...]:
    """Return retryable status codes from an override or defaults."""
    if value is None:
        return DEFAULT_RETRIABLE_CODES
    return tuple(value)


async def _post_remote_task(request: RemoteTaskRequest):
    """Post one remote task-manager request with retry handling."""
    client_timeout = Timeout(request.timeout, connect=request.timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
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
