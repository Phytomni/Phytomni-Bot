# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transition and final-report contracts for the DeepGenome SQLite store."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.test_deep_genome_store import (
    _DisappearingUmbrellaStore,
    _reserved_store,
    _seeded_store,
    _terminalizable_store,
)

from mcp_server_phytomni.agents.deep_genome.work_items import (
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
)

pytestmark = pytest.mark.unit


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
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
        updated_at = conn.execute(
            "SELECT report_updated_at FROM tasks WHERE task_id = ?",
            (reservation.umbrella_task_id,),
        ).fetchone()[0]
    assert updated_at == first.report_updated_at


def test_brief_gene_transition_publishes_a_brief_gene_only_snapshot(
    tmp_path: Path,
) -> None:
    """BriefGene success is visible before optional work is planned."""
    store, reservation = _reserved_store(tmp_path)

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
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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

    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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
    store, reservation = _reserved_store(tmp_path)
    before = store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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
    with closed_sqlite_connection(tmp_path / "tasks.db") as conn:
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
