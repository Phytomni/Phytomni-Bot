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

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

__all__ = ["sqlite_connection"]

_CONNECT_TIMEOUT_SECONDS = 10
_BUSY_TIMEOUT_MILLISECONDS = 5000


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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MILLISECONDS}")
        yield conn
    finally:
        conn.close()
