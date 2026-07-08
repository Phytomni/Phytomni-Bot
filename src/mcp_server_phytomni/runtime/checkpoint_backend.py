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

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .task_manager import resolve_tasks_db_path

_CHECKPOINTS_DB_FILENAME = "checkpoints.db"


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
    connection = aiosqlite.connect(resolved)
    return AsyncSqliteSaver(connection)
