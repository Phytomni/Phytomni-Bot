# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared DeepGenome fixtures for the HTTP run projection tests."""

from __future__ import annotations

import json
from typing import Any

from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeReservation,
    DeepGenomeStore,
)
from tests.support.sqlite import closed_sqlite_connection


def concrete_barrier_work_items() -> list[dict[str, Any]]:
    """Return the two-row barrier fixture used by routing/report tests."""
    return [
        {
            "work_item_key": "evolution_analysis",
            "analysis_type": "evolution_analysis",
        },
        {
            "work_item_key": "promoter_design",
            "analysis_type": "promoter_design_analysis",
            "section_key": "digital_design",
        },
    ]


def seed_brief_gene_plan(
    store: DeepGenomeStore,
    reservation: DeepGenomeReservation,
    gene_id: str,
) -> None:
    """Seed the common BriefGene-success and twelve-item plan fixture."""
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", gene_id, gene_id),
    )


def successful_concrete_barrier_data() -> dict[str, dict[str, str]]:
    """Return one successful row with one planned row still missing."""
    return {
        "task_0:evolution_analysis": {
            "analysis_type": "evolution_analysis",
            "status": "success",
        }
    }


def failed_concrete_barrier_data() -> dict[str, dict[str, str]]:
    """Return terminal failures for both planned barrier rows."""
    return {
        "task_0:evolution_analysis": {
            "analysis_type": "evolution_analysis",
            "status": "failed",
        },
        "task_10": {
            "analysis_type": "digital_design",
            "status": "failed",
        },
    }


def partially_failed_concrete_barrier_data() -> dict[str, dict[str, str]]:
    """Return one successful and one failed terminal barrier row."""
    return {
        "task_0:evolution_analysis": {
            "analysis_type": "evolution_analysis",
            "status": "success",
        },
        "task_10": {
            "analysis_type": "digital_design",
            "status": "failed",
        },
    }


def usable_concrete_barrier_data() -> dict[str, dict[str, str]]:
    """Return two terminal successful concrete rows."""
    return {
        "task_0:evolution_analysis": {
            "analysis_type": "evolution_analysis",
            "status": "success",
        },
        "task_10": {
            "analysis_type": "digital_design",
            "status": "success",
        },
    }


def reserve_smep_finalization(
    db_path: str,
    *,
    run_id: str = "run-1",
    umbrella_task_id: str = "task-1",
    owner: str = "alice",
    output_dir: str = "/obs/run",
) -> DeepGenomeReservation:
    """Reserve a run, seed BriefGene, and fail every non-SMEP work item."""
    store = DeepGenomeStore(db_path)
    reservation = store.reserve_run(
        run_id=run_id,
        umbrella_task_id=umbrella_task_id,
        owner=owner,
        output_dir=output_dir,
    )
    seed_brief_gene_plan(store, reservation, "Os01g0177400")
    with closed_sqlite_connection(db_path) as conn:
        conn.execute(
            "UPDATE deep_genome_remote_tasks SET status = 'failed', "
            "failure_reason = 'analysis task failed' "
            "WHERE umbrella_task_id = ? AND work_item_key != ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        )
    return reservation


def seed_partial_deep_genome_run(
    db_path: str,
    *,
    owner: str = "u1",
) -> str:
    """Seed one owner-scoped DeepGenome snapshot without a coordinator."""
    store = DeepGenomeStore(db_path)
    run_id = f"run-dg-{owner}"
    umbrella_id = f"dg-{owner}"
    reservation = store.reserve_run(
        run_id=run_id,
        umbrella_task_id=umbrella_id,
        owner=owner,
        output_dir="/obs/deep-genome",
    )
    store.apply_brief_gene_transition(
        umbrella_id,
        status="succeeded",
        summary_markdown="BriefGene profile",
    )
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    store.seed_plan(reservation, plan)
    store.apply_work_item_transition(
        umbrella_id,
        work_item_key="protein_design",
        status="failed",
    )
    store.apply_work_item_transition(
        umbrella_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="SMEP summary",
    )
    return reservation.run_id


def attach_formatted_result(db_path: str, run_id: str) -> None:
    """Add a legacy-compatible formatted block to one seeded run."""
    with closed_sqlite_connection(db_path) as conn:
        row = conn.execute(
            "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row is not None
        result = json.loads(row[0])
        result["formatted"] = {
            "answer": "stored DeepGenome answer",
            "metadata": {"consumer": "artifact-ui"},
        }
        conn.execute(
            "UPDATE runs SET result_json = ? WHERE run_id = ?",
            (json.dumps(result), run_id),
        )


def seed_terminal_deep_genome_run(
    db_path: str,
    *,
    owner: str,
    all_failed: bool,
) -> str:
    """Seed a final or all-failed DeepGenome snapshot for list tests."""
    store = DeepGenomeStore(db_path)
    run_id = f"run-dg-terminal-{owner}"
    umbrella_id = f"dg-terminal-{owner}"
    reservation = store.reserve_run(
        run_id=run_id,
        umbrella_task_id=umbrella_id,
        owner=owner,
        output_dir="/obs/deep-genome",
    )
    store.apply_brief_gene_transition(
        umbrella_id,
        status="succeeded",
        summary_markdown="BriefGene profile",
    )
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    store.seed_plan(reservation, plan)
    for item in plan:
        succeeded = not all_failed and item.work_item_key == "smep_analysis"
        store.apply_work_item_transition(
            umbrella_id,
            work_item_key=item.work_item_key,
            status="succeeded" if succeeded else "failed",
            summary_markdown="SMEP summary" if succeeded else None,
        )
    if all_failed:
        store.fail_umbrella(umbrella_id, reason="all analyses failed")
    else:
        snapshot = store.get_snapshot(umbrella_id)
        assert snapshot is not None
        store.publish_final_report(
            umbrella_id,
            final_report="# Final report\n",
            expected_revision=snapshot.report_revision,
        )
    attach_formatted_result(db_path, run_id)
    return run_id


def assert_report_metadata(
    result: dict[str, Any],
    *,
    stage: str,
    revision: int | None = None,
) -> dict[str, Any]:
    """Assert the canonical report metadata and return its execution block."""
    report = result["formatted"]["metadata"]["report"]
    assert report["state"] == stage
    assert report["source_artifact_count"] == len(
        result["execution"]["artifacts"]
    )
    if revision is not None:
        assert result["formatted"]["metadata"]["deep_genome"]["revision"] == (
            revision
        )
    assert report["degraded"] is True
    assert result["formatted"]["metadata"]["deep_genome"]["completeness"] == (
        "partial"
    )
    assert result["execution"]["tracking"]["degraded"] is True
    assert result["formatted"]["metadata"]["consumer"] == "artifact-ui"
    assert result["formatted"]["metadata"]["report"] == (
        result["execution"]["report"]
    )
    return report
