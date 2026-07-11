# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Local SQLite checkpoint backend for graph pause points.

Default checkpointer is ``AsyncSqliteSaver`` at a local
``checkpoints.db`` beside ``server_tasks.db`` (never obsfs, to
avoid WAL deadlocks). Tests inject ``MemorySaver`` via the
``ensure_checkpointer`` DI seam instead.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncIterator, Iterable
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .task_manager import resolve_tasks_db_path

_CHECKPOINTS_DB_FILENAME = "checkpoints.db"


class _DirectSqliteCursor:
    """Async-shaped cursor backed by the current event-loop thread.

    The sandbox used by the offline gate does not reliably wake an asyncio
    selector from a worker thread.  ``aiosqlite`` therefore leaves its
    completion futures pending even though SQLite has finished the query.
    Checkpoint rows are small and local, so this adapter keeps the
    ``AsyncSqliteSaver`` protocol while avoiding that cross-thread wake-up.
    """

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    async def __aenter__(self) -> _DirectSqliteCursor:
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Close the wrapped cursor when its async context exits."""
        self._cursor.close()

    async def execute(
        self, query: str, parameters: Iterable[Any] = ()
    ) -> _DirectSqliteCursor:
        """Execute one SQL statement and return this cursor."""
        self._cursor.execute(query, tuple(parameters))
        return self

    async def executemany(
        self, query: str, parameters: Iterable[Iterable[Any]]
    ) -> _DirectSqliteCursor:
        """Execute one SQL statement for each parameter row."""
        self._cursor.executemany(query, [tuple(row) for row in parameters])
        return self

    async def fetchone(self) -> tuple[Any, ...] | None:
        """Return the next result row, if one exists."""
        return self._cursor.fetchone()

    async def fetchall(self) -> list[tuple[Any, ...]]:
        """Return all remaining result rows."""
        return self._cursor.fetchall()

    def __aiter__(self) -> AsyncIterator[tuple[Any, ...]]:
        return self

    async def __anext__(self) -> tuple[Any, ...]:
        """Yield the next row for async iteration."""
        row = self._cursor.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row


class _DirectSqliteConnection:
    """Small async-compatible facade over a local ``sqlite3`` connection."""

    def __init__(self, path: str) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)

    def __await__(self) -> Any:
        async def _ready() -> _DirectSqliteConnection:
            return self

        return _ready().__await__()

    def executescript(self, script: str) -> _DirectSqliteCursor:
        """Execute a multi-statement SQL script."""
        return _DirectSqliteCursor(self._connection.executescript(script))

    def execute(
        self, query: str, parameters: Iterable[Any] = ()
    ) -> _DirectSqliteCursor:
        """Execute one SQL statement and return its cursor."""
        cursor = self._connection.execute(query, tuple(parameters))
        return _DirectSqliteCursor(cursor)

    def cursor(self) -> _DirectSqliteCursor:
        """Return a cursor with the async-shaped methods saver code expects."""
        return _DirectSqliteCursor(self._connection.cursor())

    async def commit(self) -> None:
        """Commit pending checkpoint writes."""
        self._connection.commit()

    async def close(self) -> None:
        """Close the local SQLite connection."""
        self._connection.close()


def resolve_checkpoints_db_path() -> str:
    """Return the local checkpoints db path beside the tasks db.

    Returns:
        Absolute path to ``checkpoints.db`` in the same directory as the
        run/task registry database. Local only — never an obsfs mount.
    """
    tasks_db = resolve_tasks_db_path()
    directory = os.path.dirname(tasks_db) or "."
    return os.path.join(directory, _CHECKPOINTS_DB_FILENAME)


def build_default_checkpointer(
    db_path: str | None = None,
) -> AsyncSqliteSaver:
    """Return an AsyncSqliteSaver bound to a local sqlite connection.

    The connection is opened eagerly (not via ``async with``) because
    the checkpointer is stored on an agent instance in ``__init__`` and
    must outlive that synchronous scope.

    Args:
        db_path: Optional explicit db path; defaults to
            :func:`resolve_checkpoints_db_path`.

    Returns:
        An ``AsyncSqliteSaver`` writing to the resolved local path.
    """
    resolved = db_path or resolve_checkpoints_db_path()
    connection: Any = _DirectSqliteConnection(resolved)
    return AsyncSqliteSaver(connection)
