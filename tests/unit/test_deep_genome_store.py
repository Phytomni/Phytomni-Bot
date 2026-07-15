# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the additive DeepGenome SQLite schema."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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


def _store(tmp_path: Path) -> DeepGenomeStore:
    """Build a store rooted at a temporary task registry."""
    return DeepGenomeStore(str(tmp_path / "tasks.db"))


def _reservation_counts(tmp_path: Path) -> tuple[int, int, int]:
    """Return run, umbrella-task, and BriefGene row counts."""
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        return tuple(
            conn.execute(query).fetchone()[0]
            for query in (
                "SELECT COUNT(*) FROM runs",
                "SELECT COUNT(*) FROM tasks",
                "SELECT COUNT(*) FROM deep_genome_sections",
            )
        )


class _FailingStore(DeepGenomeStore):
    """Inject one SQLite failure into the reservation transaction."""

    def __init__(self, db_path: str, fail_after: str):
        self.fail_after = fail_after
        super().__init__(db_path)

    def _reservation_execute(
        self,
        connection: sqlite3.Connection,
        stage: str,
        statement: str,
        parameters: tuple[Any, ...],
    ) -> None:
        if stage == self.fail_after:
            raise sqlite3.OperationalError(f"injected failure after {stage}")
        super()._reservation_execute(connection, stage, statement, parameters)


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


def test_reserve_run_commits_all_three_rows(tmp_path: Path) -> None:
    """Reservation commits the owner run, umbrella, and BriefGene rows."""
    reservation = _store(tmp_path).reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )

    assert reservation.run_id == "run-1"
    assert reservation.umbrella_task_id == "task-1"
    assert _reservation_counts(tmp_path) == (1, 1, 1)
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        run = conn.execute(
            "SELECT user_id, agent, origin, status, result_json "
            "FROM runs WHERE run_id = ?",
            ("run-1",),
        ).fetchone()
        task = conn.execute(
            "SELECT status, run_id, user_id, report_stage, "
            "report_completeness, report_revision FROM tasks "
            "WHERE task_id = ?",
            ("task-1",),
        ).fetchone()
        section = conn.execute(
            "SELECT section_key, section_kind, display_order, status "
            "FROM deep_genome_sections WHERE umbrella_task_id = ?",
            ("task-1",),
        ).fetchone()

    assert run[:4] == ("alice", "deep_genome", "remote", "running")
    assert '"task_id": "task-1"' in run[4]
    assert task == (
        "running",
        "run-1",
        "alice",
        "waiting_for_brief_gene",
        "none",
        0,
    )
    assert section == ("brief_gene", "brief_gene", 0, "planned")


@pytest.mark.parametrize("fail_after", ["run", "task", "brief_gene"])
def test_reservation_failure_rolls_back_every_row(
    tmp_path: Path,
    fail_after: str,
) -> None:
    """Any reservation statement failure leaves no partial local state."""
    store = _FailingStore(str(tmp_path / "tasks.db"), fail_after)

    with pytest.raises(sqlite3.Error):
        store.reserve_run(
            run_id="run-1",
            umbrella_task_id="task-1",
            owner="alice",
            output_dir="/tmp/task-1",
        )

    assert _reservation_counts(tmp_path) == (0, 0, 0)


def test_reserve_run_rejects_identity_collision(tmp_path: Path) -> None:
    """A duplicate identity raises instead of replacing durable rows."""
    store = _store(tmp_path)
    arguments = {
        "run_id": "run-1",
        "umbrella_task_id": "task-1",
        "owner": "alice",
        "output_dir": "/tmp/task-1",
    }
    store.reserve_run(**arguments)

    with pytest.raises(sqlite3.IntegrityError):
        store.reserve_run(**arguments)

    assert _reservation_counts(tmp_path) == (1, 1, 1)


def test_compensate_launch_failure_clears_seeded_profile(
    tmp_path: Path,
) -> None:
    """A failed local launch settles both parents and removes BriefGene."""
    store = _store(tmp_path)
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )

    store.compensate_launch_failure(reservation)

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        run = conn.execute(
            "SELECT status, error FROM runs WHERE run_id = ?",
            ("run-1",),
        ).fetchone()
        task = conn.execute(
            "SELECT status, final_report, degraded_reason FROM tasks "
            "WHERE task_id = ?",
            ("task-1",),
        ).fetchone()
        sections = conn.execute(
            "SELECT COUNT(*) FROM deep_genome_sections "
            "WHERE umbrella_task_id = ?",
            ("task-1",),
        ).fetchone()[0]

    assert run == ("failed", "local coordinator failed to start")
    assert task == ("failed", None, "local coordinator failed to start")
    assert sections == 0
