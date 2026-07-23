# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared SQLite contexts that commit and explicitly close test connections."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from typing import Any

__all__ = ["closed_sqlite_connection"]


@contextmanager
def closed_sqlite_connection(
    *args: Any,
    **kwargs: Any,
) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a connection, then close it deterministically."""
    with ExitStack() as stack:
        connection = stack.enter_context(
            closing(sqlite3.connect(*args, **kwargs))
        )
        stack.enter_context(connection)
        yield connection
