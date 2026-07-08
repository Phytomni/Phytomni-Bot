# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the SQLite checkpoint backend and DI fallback."""

from __future__ import annotations

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from mcp_server_phytomni.runtime.checkpoint_backend import (
    build_default_checkpointer,
    resolve_checkpoints_db_path,
)


def test_async_sqlite_saver_importable() -> None:
    """The new first-party dependency is installed and importable."""
    assert AsyncSqliteSaver is not None


async def test_build_default_checkpointer_returns_sqlite_saver(
    tmp_path,
) -> None:
    """build_default_checkpointer yields an AsyncSqliteSaver at the path."""
    db_path = str(tmp_path / "checkpoints.db")
    saver = build_default_checkpointer(db_path)
    assert isinstance(saver, AsyncSqliteSaver)


def test_resolve_checkpoints_db_path_is_local_sibling() -> None:
    """The checkpoints db path sits beside the tasks db, named correctly."""
    path = resolve_checkpoints_db_path()
    assert path.endswith("checkpoints.db")
    assert "/obs/" not in path
