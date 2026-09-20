# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared SQLite connection mechanics.

The helper deliberately does not create parent directories, initialize
schemas, set row factories, or translate database exceptions. Those policies
belong to each store so authentication and best-effort audit persistence keep
their independent failure contracts.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager

__all__ = ["sqlite_connection", "sqlite_transaction"]

_CONNECT_TIMEOUT_SECONDS = 10
_BUSY_TIMEOUT_MILLISECONDS = 5000
_WAL_INIT_LOCK = threading.Lock()


@contextmanager
def sqlite_connection(
    db_path: str,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield one short-lived autocommit SQLite connection.

    Args:
        db_path: Existing or creatable local SQLite database path.

    Yields:
        A connection configured for WAL mode and a five-second busy wait.

    Raises:
        sqlite3.Error: Connection or PRAGMA failures are propagated unchanged.
        OSError: Filesystem failures are propagated unchanged.
    """
    conn = sqlite3.connect(
        db_path,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MILLISECONDS}")
        with _WAL_INIT_LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
        yield conn
    finally:
        conn.close()


@contextmanager
def sqlite_transaction(
    db_path: str | os.PathLike[str],
    *,
    timeout: float | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield a transactional connection that is closed on exit.

    Unlike ``sqlite3.Connection``'s context manager alone, this helper also
    closes the connection after commit or rollback. The optional timeout is
    forwarded to ``sqlite3.connect`` for callers that need a longer lock
    wait while preserving the default connection behavior otherwise.

    Args:
        db_path: SQLite database path.
        timeout: Optional SQLite busy timeout in seconds.

    Yields:
        A SQLite connection whose transaction is committed on success and
        rolled back on failure, then closed deterministically.
    """
    if timeout is None:
        conn = sqlite3.connect(db_path)
    else:
        conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
