# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the additive DeepGenome SQLite schema."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission as CoordinatorRemoteSubmission,
)
from mcp_server_phytomni.agents.deep_genome.work_items import (
    WorkItemSpec,
    build_work_item_plan,
)
from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_FINAL_FAILURE_REASONS,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeSnapshot,
    DeepGenomeStore,
    DeepGenomeTrackingError,
    DeepGenomeTransitionError,
    RemoteSubmission,
    snapshot_to_formatted_report_metadata,
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


def _seeded_store(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, DeepGenomeReservation]:
    """Reserve a run, mark BriefGene successful, and seed its plan."""
    store = _store(tmp_path)
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE deep_genome_sections SET status = 'succeeded', "
            "summary_markdown = 'BriefGene' "
            "WHERE umbrella_task_id = ? AND section_key = 'brief_gene'",
            (reservation.umbrella_task_id,),
        )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
    )
    return store, reservation


def _terminalizable_store(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, DeepGenomeReservation, int]:
    """Prepare one running umbrella with one usable analysis item."""
    store, reservation = _seeded_store(tmp_path)
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    failed_keys = tuple(
        item.work_item_key
        for item in plan
        if item.work_item_key not in {"smep_analysis", "smoc_analysis"}
    )
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        for work_item_key in failed_keys:
            conn.execute(
                "UPDATE deep_genome_remote_tasks SET status = 'failed', "
                "failure_reason = 'analysis task failed' "
                "WHERE umbrella_task_id = ? AND work_item_key = ?",
                (reservation.umbrella_task_id, work_item_key),
            )
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="older summary",
    )
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    return store, reservation, snapshot.report_revision


def _invalid_plan_items(kind: str) -> list[WorkItemSpec]:
    """Return a valid plan with one selected malformed field."""
    items = list(build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"))
    if kind == "work_item_key":
        items[0] = replace(items[0], work_item_key="")
    elif kind == "target_gene":
        items[0] = replace(items[0], target_gene=" ")
    else:
        items[0] = replace(items[0], display_order=-1)
    return items


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


class _DisappearingUmbrellaStore(DeepGenomeStore):
    """Simulate an owner row disappearing during snapshot persistence."""

    @classmethod
    def _persist_report_snapshot(
        cls,
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        now: str,
    ) -> None:
        connection.execute(
            "UPDATE tasks SET status = 'failed' WHERE task_id = ?",
            (umbrella_task_id,),
        )
        super()._persist_report_snapshot(connection, umbrella_task_id, now)


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


def test_snapshot_to_formatted_report_metadata_covers_report_states() -> None:
    """The additive metadata adapter mirrors every public report state."""
    waiting = DeepGenomeSnapshot(
        umbrella_task_id="task-waiting",
        status="running",
        intermediate_report=None,
        final_report=None,
        report_stage="waiting_for_brief_gene",
        report_completeness="none",
        report_revision=0,
        report_updated_at=None,
        progress={"planning_complete": False},
        degraded=False,
        degraded_reason=None,
        failures=(),
    )
    intermediate = replace(
        waiting,
        umbrella_task_id="task-intermediate",
        report_stage="intermediate",
        report_completeness="partial",
        report_revision=2,
        report_updated_at="2026-07-16T00:00:00+00:00",
        progress={"planning_complete": True, "succeeded": 1},
    )
    partial_final = replace(
        intermediate,
        umbrella_task_id="task-partial-final",
        status="succeeded",
        report_stage="final",
        report_revision=7,
        degraded=True,
        failures=(
            {
                "work_item_key": "analysis-1",
                "status": "failed",
                "failure_reason": "private upstream detail",
            },
        ),
    )
    complete_final = replace(
        partial_final,
        umbrella_task_id="task-complete-final",
        degraded=False,
        failures=(),
    )
    all_failed = replace(
        intermediate,
        umbrella_task_id="task-all-failed",
        status="failed",
        degraded=True,
        failures=(
            {
                "work_item_key": "analysis-1",
                "status": "failed",
                "failure_reason": "private upstream detail",
            },
            {"work_item_key": "analysis-2", "status": "timed_out"},
        ),
    )

    waiting_metadata = snapshot_to_formatted_report_metadata(waiting)
    assert waiting_metadata == {
        "stage": "waiting_for_brief_gene",
        "completeness": "none",
        "revision": 0,
        "updated_at": None,
        "progress": {
            "planning_complete": False,
            "brief_gene_status": "unknown",
            "total": 0,
            "planned": 0,
            "submitted": 0,
            "pending": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "timed_out": 0,
        },
        "degraded": False,
        "failure_count": 0,
    }
    intermediate_metadata = snapshot_to_formatted_report_metadata(intermediate)
    partial_metadata = snapshot_to_formatted_report_metadata(partial_final)
    complete_metadata = snapshot_to_formatted_report_metadata(complete_final)
    failed_metadata = snapshot_to_formatted_report_metadata(all_failed)
    assert intermediate_metadata["revision"] == 2
    assert partial_metadata["degraded"] is True
    assert partial_metadata["failure_count"] == 1
    assert complete_metadata["failure_count"] == 0
    assert failed_metadata["failure_count"] == 2


def test_remote_submission_has_one_canonical_definition() -> None:
    """Coordinator and persistence layers share one immutable type."""
    assert CoordinatorRemoteSubmission is RemoteSubmission


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


def test_seed_plan_creates_eleven_sections_and_twelve_work_items(
    tmp_path: Path,
) -> None:
    """Seeding persists every logical section and concrete optional job."""
    store, reservation = _seeded_store(tmp_path)

    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.progress["planning_complete"] is True
    assert snapshot.progress["total"] == 12
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        analysis_sections = conn.execute(
            "SELECT COUNT(*) FROM deep_genome_sections "
            "WHERE umbrella_task_id = ? AND section_kind = 'analysis'",
            (reservation.umbrella_task_id,),
        ).fetchone()[0]
        work_items = conn.execute(
            "SELECT COUNT(*) FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()[0]

    assert analysis_sections == 11
    assert work_items == 12
    assert _reservation_counts(tmp_path) == (1, 1, 12)


def test_seed_plan_requires_brief_gene_success(tmp_path: Path) -> None:
    """Planning cannot expose optional work before the required profile."""
    store = _store(tmp_path)
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )

    with pytest.raises(DeepGenomeTransitionError, match="BriefGene"):
        store.seed_plan(
            reservation,
            build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
        )

    assert _reservation_counts(tmp_path) == (1, 1, 1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("work_item_key", "", "work_item_key"),
        ("target_gene", " ", "target_gene"),
        ("display_order", -1, "display_order"),
    ],
)
def test_seed_plan_rejects_malformed_item_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    """Malformed work-item metadata cannot enter the durable plan."""
    store, reservation = _seeded_store(tmp_path)
    del value
    items = _invalid_plan_items(field)

    with pytest.raises(DeepGenomeTransitionError, match=message):
        store.seed_plan(reservation, items)


def test_seed_plan_rejects_terminal_umbrella(tmp_path: Path) -> None:
    """A terminal owner cannot be replanned after completion or failure."""
    store, reservation = _seeded_store(tmp_path)
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE tasks SET status = 'failed' WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        )
        conn.execute(
            "UPDATE runs SET status = 'failed' WHERE run_id = ?",
            (reservation.run_id,),
        )

    with pytest.raises(DeepGenomeTransitionError, match="terminal"):
        store.seed_plan(
            reservation,
            build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
        )


def test_seed_plan_is_idempotent_for_identical_items(tmp_path: Path) -> None:
    """Retrying the same plan does not duplicate or rewrite child rows."""
    store, reservation = _seeded_store(tmp_path)
    items = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")

    store.seed_plan(reservation, items)

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        counts = (
            conn.execute(
                "SELECT COUNT(*) FROM deep_genome_sections "
                "WHERE umbrella_task_id = ?",
                (reservation.umbrella_task_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM deep_genome_remote_tasks "
                "WHERE umbrella_task_id = ?",
                (reservation.umbrella_task_id,),
            ).fetchone()[0],
        )

    assert counts == (12, 12)


def test_remote_identity_cannot_be_rebound(tmp_path: Path) -> None:
    """Accepted caller and polling ids remain immutable across retries."""
    store, reservation = _seeded_store(tmp_path)
    submission = RemoteSubmission("caller-1", "source-1", "obs://out")

    store.accept_remote_submission(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        submission=submission,
    )
    store.accept_remote_submission(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        submission=submission,
    )

    with pytest.raises(
        DeepGenomeTransitionError, match="identity is immutable"
    ):
        store.accept_remote_submission(
            reservation.umbrella_task_id,
            work_item_key="smep_analysis",
            submission=RemoteSubmission("caller-2", "source-1", "obs://out"),
        )

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        row = conn.execute(
            "SELECT status, submitted_task_id, poll_task_id "
            "FROM deep_genome_remote_tasks WHERE umbrella_task_id = ? "
            "AND work_item_key = ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        ).fetchone()

    assert row == ("submitted", "caller-1", "source-1")


def test_remote_identity_rejects_blank_pre_acceptance(tmp_path: Path) -> None:
    """Invalid acceptance leaves a planned item with both ids unset."""
    store, reservation = _seeded_store(tmp_path)

    with pytest.raises(DeepGenomeTransitionError, match="nonblank"):
        store.accept_remote_submission(
            reservation.umbrella_task_id,
            work_item_key="smoc_analysis",
            submission=RemoteSubmission("", "source-1", "obs://out"),
        )

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        row = conn.execute(
            "SELECT status, submitted_task_id, poll_task_id "
            "FROM deep_genome_remote_tasks WHERE umbrella_task_id = ? "
            "AND work_item_key = ?",
            (reservation.umbrella_task_id, "smoc_analysis"),
        ).fetchone()

    assert row == ("planned", None, None)


def test_remote_identity_rejects_blank_output_dir(tmp_path: Path) -> None:
    """A submission needs a usable result directory as well as ids."""
    store, reservation = _seeded_store(tmp_path)

    with pytest.raises(DeepGenomeTransitionError, match="output_dir"):
        store.accept_remote_submission(
            reservation.umbrella_task_id,
            work_item_key="smoc_analysis",
            submission=RemoteSubmission("caller-1", "source-1", ""),
        )


def test_remote_identity_rejects_terminal_umbrella(
    tmp_path: Path,
) -> None:
    """A terminal umbrella cannot accept a late remote identity."""
    store, reservation = _seeded_store(tmp_path)
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE tasks SET status = 'failed' WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        )
        conn.execute(
            "UPDATE runs SET status = 'failed' WHERE run_id = ?",
            (reservation.run_id,),
        )

    with pytest.raises(DeepGenomeTransitionError, match="terminal"):
        store.accept_remote_submission(
            reservation.umbrella_task_id,
            work_item_key="smoc_analysis",
            submission=RemoteSubmission("caller-1", "source-1", "obs://out"),
        )


def test_duplicate_transition_does_not_advance_revision(
    tmp_path: Path,
) -> None:
    """An identical observation is a durable no-op."""
    store, reservation = _seeded_store(tmp_path)

    first = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="running",
    )
    second = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="running",
    )

    assert first.report_revision == 1
    assert second.report_revision == first.report_revision
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        updated_at = conn.execute(
            "SELECT report_updated_at FROM tasks WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()[0]
    assert updated_at == first.report_updated_at


def test_brief_gene_transition_publishes_a_brief_gene_only_snapshot(
    tmp_path: Path,
) -> None:
    """BriefGene success is visible before optional work is planned."""
    store = _store(tmp_path)
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )

    snapshot = store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )

    assert snapshot.report_revision == 1
    assert snapshot.report_stage == "intermediate"
    assert snapshot.report_completeness == "partial"
    assert snapshot.intermediate_report is not None
    assert "BriefGene summary" in snapshot.intermediate_report
    assert snapshot.progress["planning_complete"] is False
    assert snapshot.progress["brief_gene_status"] == "succeeded"


def test_failed_work_item_persists_only_a_fixed_failure_reason(
    tmp_path: Path,
) -> None:
    """Remote failure text never crosses the local report boundary."""
    store, reservation = _seeded_store(tmp_path)

    snapshot = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="failed",
        failure_reason="secret DSN and upstream response body",
    )

    assert snapshot.degraded is True
    assert snapshot.degraded_reason == "1 of 12 optional analyses unavailable"
    assert snapshot.failures == (
        {
            "work_item_key": "smep_analysis",
            "status": "failed",
            "reason": "analysis task failed",
        },
    )
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        stored_reason = conn.execute(
            "SELECT failure_reason FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        ).fetchone()[0]
    assert stored_reason == "analysis task failed"


def test_transition_preserves_an_existing_sanitized_degraded_reason(
    tmp_path: Path,
) -> None:
    """A later snapshot does not erase an established local reason."""
    store, reservation = _seeded_store(tmp_path)
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE tasks SET degraded_reason = ? WHERE task_id = ?",
            ("previously sanitized", reservation.umbrella_task_id),
        )

    snapshot = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="running",
    )

    assert snapshot.degraded_reason == "previously sanitized"


def test_tracking_failure_rolls_back_when_umbrella_stops_running(
    tmp_path: Path,
) -> None:
    """A missing running owner aborts the child transition atomically."""
    _, reservation = _seeded_store(tmp_path)
    store = _DisappearingUmbrellaStore(str(tmp_path / "tasks.db"))

    with pytest.raises(
        DeepGenomeTrackingError, match="reserved umbrella task is missing"
    ):
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key="smep_analysis",
            status="running",
        )

    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        task_status = conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()[0]
        item_status = conn.execute(
            "SELECT status FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        ).fetchone()[0]
    assert task_status == "running"
    assert item_status == "planned"


def test_terminal_transition_cannot_regress_without_a_write(
    tmp_path: Path,
) -> None:
    """A terminal child cannot return to an active lifecycle state."""
    store, reservation = _seeded_store(tmp_path)
    completed = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="SMEP summary",
    )

    with pytest.raises(DeepGenomeTransitionError, match="terminal"):
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key="smep_analysis",
            status="running",
        )

    latest = store.get_snapshot(reservation.umbrella_task_id)
    assert latest is not None
    assert latest.report_revision == completed.report_revision
    assert latest.intermediate_report == completed.intermediate_report


def _run_concurrent_transitions(
    store: DeepGenomeStore,
    reservation: DeepGenomeReservation,
) -> list[DeepGenomeSnapshot]:
    """Run two independent successful updates behind one start barrier."""
    barrier = threading.Barrier(2)

    def transition(
        work_item_key: str,
        summary: str,
    ) -> DeepGenomeSnapshot:
        barrier.wait(timeout=5)
        return store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key=work_item_key,
            status="succeeded",
            summary_markdown=summary,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(transition, "smep_analysis", "SMEP summary"),
            executor.submit(transition, "smoc_analysis", "SMOC summary"),
        ]
        return [future.result() for future in futures]


def test_concurrent_successes_preserve_both_sections(tmp_path: Path) -> None:
    """Serialized SQLite writers retain both material report updates."""
    for attempt in range(10):
        attempt_path = tmp_path / str(attempt)
        attempt_path.mkdir()
        store, reservation = _seeded_store(attempt_path)
        snapshots = _run_concurrent_transitions(store, reservation)
        latest = max(snapshots, key=lambda snapshot: snapshot.report_revision)
        assert latest.intermediate_report is not None
        assert "SMEP summary" in latest.intermediate_report
        assert "SMOC summary" in latest.intermediate_report
        assert latest.report_revision == 2


def test_stale_finalizer_cannot_overwrite_newer_snapshot(
    tmp_path: Path,
) -> None:
    """Final publication uses the revision captured before synthesis."""
    store, reservation, old_revision = _terminalizable_store(tmp_path)

    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smoc_analysis",
        status="succeeded",
        summary_markdown="newer summary",
    )

    with pytest.raises(
        DeepGenomeTransitionError, match="stale report revision"
    ):
        store.publish_final_report(
            reservation.umbrella_task_id,
            final_report="# stale",
            expected_revision=old_revision,
        )

    latest = store.get_snapshot(reservation.umbrella_task_id)
    assert latest is not None
    assert latest.status == "running"
    assert latest.final_report is None
    assert latest.report_revision == old_revision + 1
    assert "newer summary" in (latest.intermediate_report or "")


def test_publish_final_report_sets_task_and_run_terminal_atomically(
    tmp_path: Path,
) -> None:
    """A usable complete snapshot settles both local owner rows."""
    store, reservation, _ = _terminalizable_store(tmp_path)
    terminal = store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smoc_analysis",
        status="failed",
    )

    snapshot = store.publish_final_report(
        reservation.umbrella_task_id,
        final_report="# DeepGenome final report\n",
        expected_revision=terminal.report_revision,
    )

    assert snapshot.status == "succeeded"
    assert snapshot.final_report == "# DeepGenome final report\n"
    assert snapshot.report_stage == "final"
    assert snapshot.report_completeness == "partial"
    assert snapshot.degraded is True
    assert snapshot.report_revision == terminal.report_revision + 1
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        run = conn.execute(
            "SELECT status, result_json, error FROM runs WHERE run_id = ?",
            (reservation.run_id,),
        ).fetchone()
        task = conn.execute(
            "SELECT status, final_report, report_stage, "
            "report_completeness FROM tasks WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()

    assert run[0] == "succeeded"
    assert run[2] is None
    assert '"final_report": "# DeepGenome final report\\n"' in run[1]
    assert task == (
        "succeeded",
        "# DeepGenome final report\n",
        "final",
        "partial",
    )


def test_publish_final_report_marks_complete_when_all_items_are_usable(
    tmp_path: Path,
) -> None:
    """A fully usable plan publishes a non-degraded complete report."""
    store, reservation = _seeded_store(tmp_path)
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    for item in build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"):
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key=item.work_item_key,
            status="succeeded",
            summary_markdown=f"summary for {item.work_item_key}",
        )
    current = store.get_snapshot(reservation.umbrella_task_id)
    assert current is not None

    snapshot = store.publish_final_report(
        reservation.umbrella_task_id,
        final_report="# complete",
        expected_revision=current.report_revision,
    )

    assert snapshot.status == "succeeded"
    assert snapshot.report_stage == "final"
    assert snapshot.report_completeness == "complete"
    assert snapshot.degraded is False
    assert snapshot.degraded_reason is None


def test_publish_final_report_rejects_nonterminal_items(
    tmp_path: Path,
) -> None:
    """A final report cannot hide concrete work that is still active."""
    store, reservation = _seeded_store(tmp_path)
    brief = store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )

    with pytest.raises(
        DeepGenomeTransitionError, match="analysis tasks are still running"
    ):
        store.publish_final_report(
            reservation.umbrella_task_id,
            final_report="# premature",
            expected_revision=brief.report_revision,
        )


def test_publish_final_report_requires_a_usable_analysis(
    tmp_path: Path,
) -> None:
    """All-terminal optional work without Markdown cannot succeed."""
    store, reservation = _seeded_store(tmp_path)
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    for item in build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"):
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key=item.work_item_key,
            status="failed",
        )
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None

    with pytest.raises(
        DeepGenomeTransitionError, match="no usable analysis result"
    ):
        store.publish_final_report(
            reservation.umbrella_task_id,
            final_report="# unusable",
            expected_revision=snapshot.report_revision,
        )


def test_failed_umbrella_keeps_intermediate_and_clears_final(
    tmp_path: Path,
) -> None:
    """Synthesis failure preserves the best report but never a final one."""
    store = _store(tmp_path)
    reservation = store.reserve_run(
        run_id="run-1",
        umbrella_task_id="task-1",
        owner="alice",
        output_dir="/tmp/task-1",
    )
    before = store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE tasks SET final_report = ? WHERE task_id = ?",
            ("stale final", reservation.umbrella_task_id),
        )

    snapshot = store.fail_umbrella(
        reservation.umbrella_task_id,
        reason="final synthesis failed",
    )

    assert isinstance(DEEP_GENOME_FINAL_FAILURE_REASONS, frozenset)
    assert len(DEEP_GENOME_FINAL_FAILURE_REASONS) == 9
    assert "final synthesis failed" in DEEP_GENOME_FINAL_FAILURE_REASONS
    assert snapshot.status == "failed"
    assert snapshot.intermediate_report == before.intermediate_report
    assert snapshot.final_report is None
    assert snapshot.report_revision == before.report_revision
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        task = conn.execute(
            "SELECT status, final_report, degraded_reason FROM tasks "
            "WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()
        run = conn.execute(
            "SELECT status, error FROM runs WHERE run_id = ?",
            (reservation.run_id,),
        ).fetchone()
    assert task == ("failed", None, "final synthesis failed")
    assert run == ("failed", "final synthesis failed")
