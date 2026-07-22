# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the explicit DeepGenome database rollback command."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from tests.agents.shared.deep_genome_fixtures import seed_brief_gene_plan

from mcp_server_phytomni.runtime.deep_genome_admin import (
    RollbackRefusedError,
    main,
    prepare_rollback,
)
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.unit


def _seed_database(tmp_path: Path, *, terminal: bool) -> Path:
    """Create one reserved DeepGenome database with twelve child rows."""
    db = tmp_path / ("terminal.db" if terminal else "running.db")
    store = DeepGenomeStore(str(db))
    reservation = store.reserve_run(
        run_id="run-dg",
        umbrella_task_id="umbrella-dg",
        owner="alice",
        output_dir="/obs/dg",
    )
    seed_brief_gene_plan(store, reservation, "Os01g0100100")
    if terminal:
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed' WHERE task_id = ?",
                (reservation.umbrella_task_id,),
            )
            conn.execute(
                "UPDATE runs SET status = 'failed', expires_at = ? "
                "WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", reservation.run_id),
            )
    return db


def _child_counts(db: Path) -> tuple[int, int, int, int]:
    """Return remote, section, task, and run row counts."""
    with sqlite3.connect(db) as conn:
        return tuple(
            conn.execute(query).fetchone()[0]
            for query in (
                "SELECT COUNT(*) FROM deep_genome_remote_tasks",
                "SELECT COUNT(*) FROM deep_genome_sections",
                "SELECT COUNT(*) FROM tasks",
                "SELECT COUNT(*) FROM runs",
            )
        )


def test_rollback_refuses_nonterminal_rows_without_acknowledgement(
    tmp_path: Path,
) -> None:
    """Running DeepGenome work is never silently discarded."""
    db = _seed_database(tmp_path, terminal=False)

    with pytest.raises(
        RollbackRefusedError,
        match="nonterminal DeepGenome runs: 1",
    ):
        prepare_rollback(str(db))

    assert _child_counts(db) == (12, 12, 1, 1)


def test_acknowledged_rollback_marks_and_removes_children(
    tmp_path: Path,
) -> None:
    """The explicit acknowledgement fails owners before child cleanup."""
    db = _seed_database(tmp_path, terminal=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET final_report = '# stale' "
            "WHERE task_id = 'umbrella-dg'"
        )
        conn.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = 'run-dg'",
            ('{"live_status": [{"status": "running"}]}',),
        )

    result = prepare_rollback(str(db), mark_nonterminal_failed=True)

    assert result.database_path == str(db)
    assert result.remote_tasks_deleted == 12
    assert result.sections_deleted == 12
    assert _child_counts(db) == (0, 0, 1, 1)
    with sqlite3.connect(db) as conn:
        task = conn.execute(
            "SELECT status, intermediate_report, degraded_reason "
            ", final_report FROM tasks "
            "WHERE task_id = 'umbrella-dg'"
        ).fetchone()
        run = conn.execute(
            "SELECT status, result_json, error FROM runs "
            "WHERE run_id = 'run-dg'"
        ).fetchone()
    assert task[0] == "failed"
    assert task[1].startswith("#")
    assert task[2] == "workflow interrupted by rollback preparation"
    assert task[3] is None
    assert run[0] == "failed"
    assert json.loads(run[1]) == {
        "artifacts": [],
        "degraded": True,
        "final_report": None,
        "live_status": [
            {
                "final_report": None,
                "output_dir": "/obs/dg",
                "status": "failed",
                "task_id": "umbrella-dg",
            }
        ],
        "task_results": [
            {
                "final_report": None,
                "output_dir": "/obs/dg",
                "status": "failed",
                "task_id": "umbrella-dg",
            }
        ],
    }
    assert run[2] == "workflow interrupted by rollback preparation"


def test_backup_restores_wal_consistent_database_and_mode(
    tmp_path: Path,
) -> None:
    """Rollback preparation creates a restorable, mode-preserving backup."""
    db = _seed_database(tmp_path, terminal=True)
    os.chmod(db, 0o640)

    result = prepare_rollback(str(db))

    backup = Path(result.backup_path)
    assert backup.exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o640
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _child_counts(db) == (0, 0, 1, 1)

    with sqlite3.connect(backup) as source, sqlite3.connect(db) as target:
        source.backup(target)
    assert _child_counts(db) == (12, 12, 1, 1)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_admin_main_returns_refusal_exit_code(tmp_path: Path) -> None:
    """The console command maps an unacknowledged refusal to exit 2."""
    db = _seed_database(tmp_path, terminal=False)

    assert main(["prepare-deep-genome-rollback", "--db", str(db)]) == 2
    assert _child_counts(db) == (12, 12, 1, 1)


def test_purge_expired_deep_genome_leaves_no_child_orphans(
    tmp_path: Path,
) -> None:
    """Retention removes concrete and logical DeepGenome rows first."""
    db = _seed_database(tmp_path, terminal=True)

    assert RunRegistry(str(db)).purge_expired() == 1
    assert _child_counts(db) == (0, 0, 0, 0)


def test_admin_main_succeeds_for_terminal_database(tmp_path: Path) -> None:
    """The console command returns zero after safe terminal cleanup."""
    db = _seed_database(tmp_path, terminal=True)

    assert main(["prepare-deep-genome-rollback", "--db", str(db)]) == 0
    assert _child_counts(db) == (0, 0, 1, 1)
