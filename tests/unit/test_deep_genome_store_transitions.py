# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transition and final-report contracts for the DeepGenome SQLite store."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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
from mcp_server_phytomni.runtime.deep_genome_report_snapshot import (
    assemble_intermediate_report,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeSnapshot,
    DeepGenomeStore,
    DeepGenomeTrackingError,
    DeepGenomeTransitionError,
)
from mcp_server_phytomni.runtime.deep_genome_store_projection import (
    snapshot_to_canonical_result,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

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


def _completed_store(
    tmp_path: Path,
) -> tuple[DeepGenomeStore, DeepGenomeReservation, DeepGenomeSnapshot]:
    """Finish twelve concrete analyses while leaving final synthesis open."""
    store, reservation = _seeded_store(tmp_path)
    for item in build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"):
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key=item.work_item_key,
            status="succeeded",
            summary_markdown=(
                f"Evidence for {item.work_item_key} <sup>2</sup>."
            ),
        )
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    return store, reservation, snapshot


def store_run_result(
    store: DeepGenomeStore,
    reservation: DeepGenomeReservation,
    result: dict[str, Any],
) -> None:
    """Seed a synthetic canonical input for persistence and polling tests."""
    with closed_sqlite_connection(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = ?",
            (json.dumps(result), reservation.run_id),
        )


def test_failed_result_persists_canonical_science_in_one_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed synthesis stores the authoritative report, not a thin ack."""
    store, reservation, before = _completed_store(tmp_path)
    references = [
        {"file_id": "unused", "title": "Unused paper"},
        {"file_id": "evidence", "title": "Evidence paper"},
    ]
    artifact = {
        "name": "analysis.tsv",
        "role": "scientific_table",
        "media_type": "text/tab-separated-values",
        "downloadable": True,
        "download_ref": "/obs/synthetic/analysis.tsv",
    }
    store_run_result(
        store,
        reservation,
        {
            "formatted": {"references": references},
            "execution": {
                "artifacts": [artifact],
                "output_dirs": ["/obs/synthetic"],
            },
        },
    )

    connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def recording_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", recording_connect)
        snapshot = store.fail_umbrella(
            reservation.umbrella_task_id, reason="final synthesis failed"
        )
    with closed_sqlite_connection(store.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id = ?",
            (reservation.run_id,),
        ).fetchone() == ("failed",)
        result = json.loads(
            connection.execute(
                "SELECT result_json FROM runs WHERE run_id = ?",
                (reservation.run_id,),
            ).fetchone()[0]
        )
    assert set(result) == {"formatted", "execution"}
    assert snapshot.status == "failed"
    assert result["formatted"]["answer"] == before.intermediate_report
    assert result["formatted"]["references"] == references
    assert result["execution"]["artifacts"] == [artifact]
    assert result["execution"]["output_dirs"] == ["/obs/synthetic"]
    assert result["execution"]["tasks"] == [
        {
            "id": reservation.umbrella_task_id,
            "accepted": True,
            "status": "failed",
        }
    ]
    assert result["execution"]["report"] == {
        "state": "intermediate",
        "degraded": True,
        "source_artifact_count": 1,
    }
    metadata = result["formatted"]["metadata"]["deep_genome"]
    assert metadata["revision"] == before.report_revision
    assert metadata["progress"]["succeeded"] == 12
    assert metadata["progress"]["failed"] == metadata["failure_count"] == 0
    assert "deep_genome_report_degraded" in {
        warning["code"] for warning in result["execution"]["warnings"]
    }
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_failed_result_rolls_back_task_when_run_write_fails(
    tmp_path: Path,
) -> None:
    """A failed owner write cannot leave an independently failed umbrella."""
    store, reservation, before = _completed_store(tmp_path)
    with closed_sqlite_connection(store.db_path) as connection:
        previous = connection.execute(
            "SELECT status, result_json, error FROM runs WHERE run_id = ?",
            (reservation.run_id,),
        ).fetchone()
        connection.execute(
            "CREATE TRIGGER reject_failure BEFORE UPDATE ON runs "
            "WHEN NEW.status = 'failed' BEGIN "
            "SELECT RAISE(ABORT, 'injected owner write failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected owner"):
        store.fail_umbrella(
            reservation.umbrella_task_id, reason="final synthesis failed"
        )
    assert store.get_snapshot(reservation.umbrella_task_id) == before
    with closed_sqlite_connection(store.db_path) as connection:
        assert (
            connection.execute(
                "SELECT status, result_json, error FROM runs WHERE run_id = ?",
                (reservation.run_id,),
            ).fetchone()
            == previous
        )


@pytest.mark.parametrize("contender", ["finalize", "cancel"])
def test_failed_result_respects_competing_terminal_settlement(
    tmp_path: Path, contender: str
) -> None:
    """Serialized terminal writers cannot replace the winning settlement."""
    store, reservation, before = _completed_store(tmp_path)
    registry = RunRegistry(store.db_path)
    barrier = threading.Barrier(2)

    def fail() -> bool:
        barrier.wait(timeout=5)
        try:
            store.fail_umbrella(
                reservation.umbrella_task_id, reason="final synthesis failed"
            )
        except DeepGenomeTransitionError:
            return False
        return True

    def compete() -> bool:
        barrier.wait(timeout=5)
        if contender == "cancel":
            return registry.settle_run(
                reservation.run_id,
                owner="alice",
                status="cancelled",
                result=None,
                expected_revision=0,
            )
        try:
            store.publish_final_report(
                reservation.umbrella_task_id,
                final_report="# Final scientific report",
                expected_revision=before.report_revision,
            )
        except DeepGenomeTransitionError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fail), executor.submit(compete)]
        won_failure, won_competitor = [
            future.result(timeout=15) for future in futures
        ]
    assert won_failure != won_competitor
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    run = registry.get_run(reservation.run_id, owner="alice")
    assert run is not None
    if won_failure:
        assert run.status == snapshot.status == "failed"
        assert snapshot.report_revision == before.report_revision
        assert run.result is not None
        assert run.result["formatted"]["answer"] == before.intermediate_report
    elif contender == "finalize":
        assert run.status == snapshot.status == "succeeded"
        assert snapshot.report_revision == before.report_revision + 1
    else:
        assert run.status == "cancelled"
        assert snapshot.status == "running"
        assert snapshot.report_revision == before.report_revision


@pytest.mark.parametrize("source", ["complete", "partial", "absent"])
@pytest.mark.parametrize("has_references", [True, False])
def test_historical_reconstruction_is_assessed_without_mutating_reads(
    tmp_path: Path, source: str, has_references: bool
) -> None:
    """Incomplete historical source cannot authorize a destructive cleanup."""
    store, reservation, before = _completed_store(tmp_path)
    assert before.intermediate_report is not None
    mixed = before.intermediate_report + (
        "\n\nHistorical author paragraph absent from structured rows."
        "\n\n### Unavailable: promoter_design\n\nanalysis task failed."
    )
    references = (
        [{"file_id": "unused"}, {"file_id": "evidence"}]
        if has_references
        else []
    )
    with closed_sqlite_connection(store.db_path) as connection:
        connection.execute(
            "UPDATE tasks SET intermediate_report = ? WHERE task_id = ?",
            (mixed, reservation.umbrella_task_id),
        )
        if source == "partial":
            connection.execute(
                "UPDATE deep_genome_remote_tasks SET summary_markdown = NULL "
                "WHERE umbrella_task_id = ? AND work_item_key = ?",
                (reservation.umbrella_task_id, "smep_analysis"),
            )
        elif source == "absent":
            connection.execute(
                "DELETE FROM deep_genome_sections WHERE umbrella_task_id = ?",
                (reservation.umbrella_task_id,),
            )
        rows = getattr(store, "_report_rows_for_connection")(
            connection, reservation.umbrella_task_id
        )
    reconstructed = assemble_intermediate_report(rows)
    stored = store.get_snapshot(reservation.umbrella_task_id)
    assert stored is not None
    result = snapshot_to_canonical_result(
        stored, existing_result={"formatted": {"references": references}}
    )
    assert "Historical author paragraph" in result["formatted"]["answer"]
    assert "### Unavailable:" in result["formatted"]["answer"]
    assert bool(result["formatted"]["references"]) == has_references
    assert reconstructed != mixed
    if source == "complete":
        assert reconstructed == before.intermediate_report
    elif source == "partial":
        assert reconstructed is not None
        assert "Evidence for smep_analysis" not in reconstructed
    else:
        assert reconstructed is None
    assert store.get_snapshot(reservation.umbrella_task_id) == stored
    assert stored.report_revision == before.report_revision
    assert stored.report_updated_at == before.report_updated_at
    assert stored.intermediate_report == mixed


def test_historical_final_report_keeps_precedence_without_read_mutation(
    tmp_path: Path,
) -> None:
    """Existing final reports take precedence over reconstructable inputs."""
    store, reservation, before = _completed_store(tmp_path)
    final = "Final scientific discussion of failed experiments <sup>2</sup>."
    with closed_sqlite_connection(store.db_path) as connection:
        connection.execute(
            "UPDATE tasks SET status = 'succeeded', final_report = ?, "
            "report_stage = 'final', report_completeness = 'complete' "
            "WHERE task_id = ?",
            (final, reservation.umbrella_task_id),
        )
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    result = snapshot_to_canonical_result(
        snapshot,
        existing_result={
            "formatted": {
                "references": [{"file_id": "unused"}, {"file_id": "evidence"}]
            }
        },
    )
    assert result["formatted"]["answer"] == final.replace("<sup>2", "<sup>1")
    assert result["formatted"]["references"][0]["file_id"] == "evidence"
    assert store.get_snapshot(reservation.umbrella_task_id) == snapshot
    assert snapshot.intermediate_report == before.intermediate_report
    assert snapshot.report_revision == before.report_revision
