# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides a TaskManager class for managing tasks in a
SQLite database and functions for interacting with a remote task server."""

import asyncio
import sqlite3
import uuid
from random import uniform
from typing import List, Optional

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPStatusError,
    Timeout,
    TimeoutException,
)
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

DEFAULT_RETRIABLE_CODES = (429, 500, 502, 503, 504)


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
        """Initializes the database and creates the tasks table if it
        doesn't exist."""
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
        """Returns a connection to the SQLite database."""
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
            (status, task_id, analysis_id, output_dir),
        )
        conn.commit()
        conn.close()


async def create_task(
    url,
    server_id: str,
    server_status: str,
    tool_name: str,
    timeout: float = 60,
    retriable_codes: Optional[List[int]] = None,
    max_retries: int = 5,
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
    if retriable_codes is None:
        retriable_codes = list(DEFAULT_RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    data = {
        "server_id": server_id,
        "server_status": server_status,
        "tool_name": tool_name,
    }
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url,
                    data=data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to rerank: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR, message=f"Network error: {str(e)}"
                    )
                ) from e


async def update_task(
    url,
    server_id: str,
    server_status: str,
    server_file_path: str,
    tool_result: str,
    timeout: float = 60,
    retriable_codes: Optional[List[int]] = None,
    max_retries: int = 5,
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
    if retriable_codes is None:
        retriable_codes = list(DEFAULT_RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    data = {
        "server_id": server_id,
        "server_status": server_status,
        "server_file_path": server_file_path,
        "tool_result": tool_result,
    }
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url,
                    data=data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to rerank: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR, message=f"Network error: {str(e)}"
                    )
                ) from e
