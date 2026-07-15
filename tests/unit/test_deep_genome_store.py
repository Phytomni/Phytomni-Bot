# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the additive DeepGenome SQLite schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeSnapshot,
    DeepGenomeStore,
)

pytestmark = pytest.mark.unit

_REPORT_COLUMNS = {
    "intermediate_report",
    "report_revision",
    "report_stage",
    "report_completeness",
    "report_updated_at",
    "progress_json",
}


def _legacy_db(tmp_path: Path) -> Path:
    """Create the original four-column task registry schema."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                analysis_id TEXT,
                output_dir TEXT
            )
            """)
    return db_path


def test_store_upgrades_legacy_database_idempotently(tmp_path: Path) -> None:
    """Repeated store construction leaves one complete schema."""
    db_path = _legacy_db(tmp_path)

    DeepGenomeStore(str(db_path))
    DeepGenomeStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        task_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tasks)")
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        foreign_keys = {
            row[2]
            for row in conn.execute(
                "PRAGMA foreign_key_list(deep_genome_remote_tasks)"
            )
        }

    assert task_columns >= _REPORT_COLUMNS
    assert {
        "deep_genome_sections",
        "deep_genome_remote_tasks",
    } <= tables
    assert foreign_keys == {"deep_genome_sections"}


def test_store_fresh_schema_has_report_columns_and_checks(
    tmp_path: Path,
) -> None:
    """Fresh databases expose nullable report fields and child checks."""
    db_path = tmp_path / "fresh.db"
    DeepGenomeStore(str(db_path))

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]: row[3] for row in conn.execute("PRAGMA table_info(tasks)")
        }
        section_sql = conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='deep_genome_sections'
            """).fetchone()[0]
        remote_sql = conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='deep_genome_remote_tasks'
            """).fetchone()[0]

    assert columns.keys() >= _REPORT_COLUMNS
    assert all(columns[name] == 0 for name in _REPORT_COLUMNS)
    assert "PRIMARY KEY (umbrella_task_id, section_key)" in section_sql
    assert "PRIMARY KEY (umbrella_task_id, work_item_key)" in remote_sql
    assert "FOREIGN KEY (umbrella_task_id, section_key)" in remote_sql


def test_store_exports_frozen_contract_models() -> None:
    """The public reservation and snapshot DTOs are immutable."""
    reservation = DeepGenomeReservation(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )
    snapshot = DeepGenomeSnapshot(
        umbrella_task_id="task-1",
        status="running",
        intermediate_report=None,
        final_report=None,
        report_stage="waiting_for_brief_gene",
        report_completeness="none",
        report_revision=0,
        report_updated_at=None,
        progress={},
        degraded=False,
        degraded_reason=None,
        failures=(),
    )

    with pytest.raises(AttributeError):
        reservation.owner = "bob"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.status = "failed"  # type: ignore[misc]
