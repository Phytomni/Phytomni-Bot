# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the SQLite checkpoint backend and DI fallback."""

from __future__ import annotations

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def test_async_sqlite_saver_importable() -> None:
    """The new first-party dependency is installed and importable."""
    assert AsyncSqliteSaver is not None
