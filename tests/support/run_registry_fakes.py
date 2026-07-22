# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral run-registry fixtures for server and unit tests."""

from __future__ import annotations

import sqlite3
from typing import Any

from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

__all__ = [
    "seed_foreign_run",
    "stamp_run_created_at",
]


def seed_foreign_run(
    db_path: str,
    *,
    run_id: str,
    user_id: str,
    agent: str,
    origin: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Insert a terminal run owned by a different user."""
    RunRegistry(db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id=user_id,
            agent=agent,
            origin=origin,
        ),
        outcome=RunOutcome(status="succeeded", result=result),
    )


def stamp_run_created_at(
    db_path: str, old_run_id: str, new_run_id: str
) -> None:
    """Set deterministic timestamps used by created-after filtering tests."""
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", old_run_id),
                ("2026-06-01T00:00:00+00:00", new_run_id),
            ],
        )
