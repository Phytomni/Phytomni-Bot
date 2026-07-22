# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared schema fixtures for task-registry migration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = [
    "EXPECTED_TASK_COLUMNS",
    "REPORT_COLUMNS",
    "create_legacy_task_db",
]


REPORT_COLUMNS = frozenset(
    {
        "intermediate_report",
        "report_revision",
        "report_stage",
        "report_completeness",
        "report_updated_at",
        "progress_json",
    }
)

EXPECTED_TASK_COLUMNS = frozenset(
    {
        "task_id",
        "status",
        "analysis_id",
        "output_dir",
        "run_id",
        "user_id",
        "agent",
        "origin",
        "created_at",
        "updated_at",
        "input_fingerprint",
        "task_log",
        "final_report",
        "degraded_reason",
        "source_task_id",
        *REPORT_COLUMNS,
    }
)


def create_legacy_task_db(
    db_path: Path,
    *,
    seed: tuple[str, str, str, str] | None = None,
) -> None:
    """Create the original task table and optionally seed one row."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tasks ("
            "task_id TEXT PRIMARY KEY, "
            "status TEXT, "
            "analysis_id TEXT, "
            "output_dir TEXT)"
        )
        if seed is not None:
            conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?)",
                seed,
            )
