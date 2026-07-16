# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared DeepGenome fixtures for the HTTP run projection tests."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
)
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore


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
    with sqlite3.connect(db_path) as conn:
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
    """Assert the common report metadata contract and return its block."""
    report = result["formatted"]["metadata"]["report"]
    assert report["stage"] == stage
    assert report["completeness"] == "partial"
    if revision is not None:
        assert report["revision"] == revision
    assert report["degraded"] is True
    assert report["failure_count"] == len(result["failures"])
    assert result["formatted"]["metadata"]["consumer"] == "artifact-ui"
    return report
